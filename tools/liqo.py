"""Liqo tool: installation, peering and namespace offloading."""

import shutil
from typing import List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.log import get_logger
from core.types import Settings
from tools.base import AnyCNI, AnyRuntime, BaseTool, BaseToolSpec, check_known_clusters
from cnis.base import find_object
from cnis.calico import CalicoCNI
from cnis.cilium import CiliumCNI
from utils.commands import CommandError, run_command
from utils.kubeconfig import get_kubeconfig_location
from utils.kubernetes_utils import ensure_kubernetes_namespace
from utils.retry import BackoffConfig, retry_with_backoff

log = get_logger(__name__)

# Node selector requirement matching every node except Liqo virtual nodes
NOT_ON_LIQO_VIRTUAL_NODES = {
    "key": "liqo.io/type",
    "operator": "DoesNotExist",
}

# Interfaces Cilium may use when Liqo is installed, if the Cilium spec does not
# list them: the node interfaces of every runtime (kind/k3d/minikube docker:
# eth0, minikube kvm2: eth0/eth1), excluding the Liqo ones (liqo.*).
LIQO_CILIUM_DEVICES = ["eth+"]

# `liqoctl install` provider for each supported runtime
LIQOCTL_PROVIDERS = {
    "k3d": "k3s",
    "kind": "kind",
    # minikube bootstraps the cluster with kubeadm
    "minikube": "kubeadm",
}

# Liqo chart value: WireGuard implementation of the gateways, "kernel"
# (default) or "userspace"
WIREGUARD_IMPLEMENTATION_VALUE = "networking.gatewayTemplates.wireguard.implementation"


class LiqoInstallationConfig(BaseModel):
    """A cluster where Liqo is installed."""

    model_config = ConfigDict(extra="forbid")

    cluster: str
    # Liqo version: a release (e.g. "v1.3.0", default: the liqoctl version),
    # "latest", or "<repo-url>@<commit-sha>" for a development version
    version: Optional[str] = None
    offloadings: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_version(self):
        """Rejects malformed development versions."""
        self.chart_version()
        return self

    def chart_version(self) -> tuple[str | None, str | None]:
        """(--repo-url, --version) liqoctl arguments, None if not set."""
        if self.version is None or self.version == "latest":
            return None, None
        if "@" in self.version:
            repo_url, _, commit = self.version.rpartition("@")
            if not repo_url or not commit:
                raise ValueError(
                    f"invalid Liqo version '{self.version}': "
                    "expected '<repo-url>@<commit-sha>'"
                )
            return repo_url, commit
        return None, self.version


class LiqoToolSpec(BaseToolSpec):
    """Parameters of the Liqo tool."""

    installations: List[LiqoInstallationConfig] = Field(default_factory=list)
    peerings: List[Tuple[str, str]] = Field(default_factory=list)
    # Retry policy of `liqoctl install` (exponential backoff between attempts)
    install_retry: BackoffConfig = Field(default_factory=BackoffConfig)

    def validate_clusters(self, clusters: list[str]) -> None:
        installed = [inst.cluster for inst in self.installations]
        check_known_clusters(installed, clusters)
        not_installed = sorted(
            {c for peering in self.peerings for c in peering} - set(installed)
        )
        if not_installed:
            raise ValueError(
                "peerings refer to clusters without a Liqo installation: "
                f"{not_installed}"
            )


class LiqoTool(BaseTool[LiqoToolSpec]):
    """Installs Liqo with liqoctl, peers the clusters and offloads namespaces."""

    def check_dependencies(self) -> None:
        if self.spec.installations and shutil.which("liqoctl") is None:
            raise RuntimeError("liqoctl is not installed or not found in PATH")

    def before_all_operations(
        self,
        all_settings: dict[str, Settings],
        all_runtimes: dict[str, AnyRuntime],
        all_cnis: dict[str, AnyCNI],
    ) -> None:
        # Runs ONCE in total, before any cluster is created: fails early if
        # Liqo cannot be installed on some cluster
        unsupported = {
            inst.cluster: all_settings[inst.cluster].runtime
            for inst in self.spec.installations
            if all_settings[inst.cluster].runtime not in LIQOCTL_PROVIDERS
        }
        if unsupported:
            raise ValueError(
                f"Liqo installation not supported on clusters {unsupported} "
                f"(supported runtimes: {sorted(LIQOCTL_PROVIDERS)})"
            )

    def _get_installation(self, cluster: str) -> LiqoInstallationConfig | None:
        """Installation of `cluster`, if any."""
        return next(
            (inst for inst in self.spec.installations if inst.cluster == cluster),
            None,
        )

    def pre_cni_install(
        self, settings: Settings, runtime: AnyRuntime, cni: AnyCNI
    ) -> None:
        # Runs once PER CLUSTER, before the CNI is installed: adapts the CNI
        # configuration to Liqo (https://docs.liqo.io/en/v1.3.0/installation/install.html),
        # only if the cluster is listed in `installations`.
        if self._get_installation(runtime.name) is None:
            return

        # The CNI node agents must not be scheduled on Liqo virtual nodes
        cni.add_node_selector_requirement(NOT_ON_LIQO_VIRTUAL_NODES)

        if isinstance(cni, CalicoCNI):
            # Calico must not pick the Liqo interfaces as node address
            cni.add_patch(_calico_skip_liqo_interfaces)

        if isinstance(cni, CiliumCNI):
            # Cilium must not attach its eBPF programs to, or read the MTU
            # from, the Liqo interfaces
            cni.add_patch(_cilium_exclude_liqo_interfaces)
            if cni.spec.kube_proxy_replacement:
                log.warning(
                    "Cilium replaces kube-proxy: offloaded pods cannot be "
                    "exposed through NodePort/LoadBalancer services",
                    cluster=runtime.name,
                )

    def post_cni_install(
        self, settings: Settings, runtime: AnyRuntime, cni: AnyCNI
    ) -> None:
        # Runs once PER CLUSTER, after the CNI is installed: installs Liqo
        # (liqoctl install) only if the cluster is listed in `installations`.
        installation = self._get_installation(runtime.name)
        if installation is None:
            return

        # liqoctl provider and its provider-specific arguments (the supported
        # runtimes are checked in before_all_operations)
        provider = LIQOCTL_PROVIDERS[settings.runtime]
        match provider:
            case "k3s":
                provider_args = [
                    "--api-server-url",
                    runtime.get_api_server_address(),
                    "--pod-cidr",
                    settings.cluster_cidr,
                    "--service-cidr",
                    settings.service_cidr,
                ]
            case "kubeadm":
                # liqoctl reads the pod/service CIDRs from the
                # kube-controller-manager, and the API server URL from the
                # kubeconfig, leaving it unset if it is a localhost address
                # (e.g. port forwarded by the docker driver): set it to the
                # node address, reachable from the other clusters. --set
                # values are applied last, overriding the detected one.
                provider_args = [
                    "--set-string",
                    f"apiServer.address={runtime.get_api_server_address()}",
                ]
            case _:
                # kind: CIDRs from the kube-controller-manager, API server
                # address from the control plane node
                provider_args = []

        if not runtime.kernel_wireguard:
            # Otherwise the gateway cannot create its tunnel interface
            # ("operation not supported") and crash-loops
            cluster_log = log.bind(cluster=runtime.name)
            cluster_log.info("No WireGuard in the node kernel: Liqo uses userspace")
            provider_args += ["--set", f"{WIREGUARD_IMPLEMENTATION_VALUE}=userspace"]

        self._install_in_cluster(
            installation=installation,
            kubeconfig=get_kubeconfig_location(runtime.name),
            provider=provider,
            provider_args=provider_args,
        )

    def after_all_operations(
        self,
        all_settings: dict[str, Settings],
        all_runtimes: dict[str, AnyRuntime],
        all_cnis: dict[str, AnyCNI],
    ) -> None:
        # Runs ONCE in total, after Liqo is installed on every cluster:
        # 1. peers each pair in `peerings` (liqoctl peer)
        # 2. offloads the namespaces listed in each installation's `offloadings`
        #    (liqoctl offload namespace), creating them if missing.
        # Workloads declared in the config are created only after this hook.
        for consumer, provider in self.spec.peerings:
            # The gateway server runs on the provider cluster: exposed through
            # a LoadBalancer service where the runtime implements them
            self._peer_clusters(
                consumer=consumer,
                provider=provider,
                gw_server_service_type="LoadBalancer"
                if all_runtimes[provider].provides_load_balancer
                else "NodePort",
            )

        for installation in self.spec.installations:
            for namespace in installation.offloadings:
                self._offload_namespace(installation.cluster, namespace)

    def _install_in_cluster(
        self,
        installation: LiqoInstallationConfig,
        kubeconfig: str,
        provider: str,
        provider_args: list[str],
    ) -> None:
        """Runs liqoctl install, retried with exponential backoff."""
        repo_url, version = installation.chart_version()
        cluster_log = log.bind(cluster=installation.cluster)

        command = ["liqoctl", "install", provider, "--verbose"]
        parameters = {
            "--cluster-id": installation.cluster,
            "--kubeconfig": kubeconfig,
            "--repo-url": repo_url,
            "--version": version,
        }
        for param, value in parameters.items():
            if value is not None:
                command.extend([param, value])
        command.extend(provider_args)

        cluster_log.info(
            "Installing Liqo",
            provider=provider,
            version=installation.version or "liqoctl default",
        )
        retry_with_backoff(
            lambda: run_command(command),
            policy=self.spec.install_retry,
            retry_on=CommandError,
            description="Liqo installation",
            logger=cluster_log,
        )
        cluster_log.info("Liqo installed")

    def _peer_clusters(
        self, consumer: str, provider: str, gw_server_service_type: str
    ) -> None:
        """Runs liqoctl peer: `consumer` uses the resources of `provider`."""
        command = [
            "liqoctl",
            "peer",
            "--kubeconfig",
            get_kubeconfig_location(consumer),
            "--remote-kubeconfig",
            get_kubeconfig_location(provider),
            "--gw-server-service-type",
            gw_server_service_type,
        ]
        log.info(
            "Peering clusters",
            consumer=consumer,
            provider=provider,
            gw_server_service_type=gw_server_service_type,
        )
        run_command(command)

    def _offload_namespace(self, cluster: str, namespace: str) -> None:
        """Offloads `namespace` of `cluster`, creating it if missing."""
        kubeconfig = get_kubeconfig_location(cluster)
        # Create the namespace if it does not exist
        ensure_kubernetes_namespace(kubeconfig, namespace)

        command = [
            "liqoctl",
            "offload",
            "namespace",
            namespace,
            "--kubeconfig",
            kubeconfig,
        ]
        log.info("Offloading namespace", cluster=cluster, namespace=namespace)
        run_command(command)


def _calico_skip_liqo_interfaces(resources: list[dict]) -> None:
    """Calico patch: the node address is never a Liqo interface."""
    installation = find_object(resources, "Installation", "default")
    installation["spec"]["calicoNetwork"]["nodeAddressAutodetectionV4"] = {
        "skipInterface": "liqo.*"
    }


def _cilium_exclude_liqo_interfaces(values: dict) -> None:
    # Explicit device list (the user one, if set in the Cilium spec), so that
    # Cilium does not auto-detect the Liqo interfaces
    values.setdefault("devices", list(LIQO_CILIUM_DEVICES))


module = LiqoTool
spec = LiqoToolSpec
