import subprocess
from typing import Type, List, Optional, Tuple, Dict

from pydantic import BaseModel, Field

from core.types import Settings
from tools.base import BaseTool
from runtimes.base import BaseRuntime
from cnis.base import BaseCNI, find_object
from cnis.calico import CalicoCNI
from utils.kubeconfig import get_kubeconfig_location
from utils.kubernetes_utils import ensure_kubernetes_namespace


# Node selector requirement matching every node except Liqo virtual nodes
NOT_ON_LIQO_VIRTUAL_NODES = {
    "key": "liqo.io/type",
    "operator": "DoesNotExist",
}


class LiqoInstallationConfig(BaseModel):
    cluster: str
    version: Optional[str] = None
    offloadings: List[str] = Field(default_factory=list)


class LiqoToolSpec(BaseModel):
    installations: List[LiqoInstallationConfig] = Field(default_factory=list)
    peerings: List[Tuple[str, str]] = Field(default_factory=list)


class LiqoTool(BaseTool[LiqoToolSpec]):
    # TODO: check dependencies

    def pre_cluster_init(
        self, settings: Settings, runtime: BaseRuntime, cni: BaseCNI
    ) -> None:
        # Runs once PER CLUSTER, before cluster creation. Currently a no-op.
        # TODO: check if the clusters are supported
        pass

    def _get_installation(self, cluster: str) -> LiqoInstallationConfig | None:
        return next(
            (inst for inst in self.spec.installations if inst.cluster == cluster),
            None,
        )

    def pre_cni_install(
        self, settings: Settings, runtime: BaseRuntime, cni: BaseCNI
    ) -> None:
        # Runs once PER CLUSTER, before the CNI is installed: adapts the CNI
        # configuration to Liqo, only if the cluster is listed in
        # `installations`.
        if self._get_installation(runtime.name) is None:
            return

        # The CNI node agents must not be scheduled on Liqo virtual nodes
        cni.add_node_selector_requirement(NOT_ON_LIQO_VIRTUAL_NODES)

        if isinstance(cni, CalicoCNI):
            # Calico must not pick the Liqo interfaces as node address
            cni.add_patch(_calico_skip_liqo_interfaces)

    def post_cni_install(
        self, settings: Settings, runtime: BaseRuntime, cni: BaseCNI
    ) -> None:
        # Runs once PER CLUSTER, after the CNI is installed: installs Liqo
        # (liqoctl install) only if the cluster is listed in `installations`.
        cluster_installation = self._get_installation(runtime.name)
        if cluster_installation is None:
            return

        match settings.runtime:
            case "k3d":
                return self._install_in_cluster(
                    runtime="k3s",
                    cluster_id=settings.name,
                    kubeconfig=get_kubeconfig_location(runtime.name),
                    version=cluster_installation.version,
                    api_server_url=runtime.get_api_server_address(),
                    pod_cidr=settings.cluster_cidr,
                    service_cidr=settings.service_cidr,
                )
            case "kind":
                return self._install_in_cluster(
                    runtime="kind",
                    cluster_id=settings.name,
                    kubeconfig=get_kubeconfig_location(runtime.name),
                    version=cluster_installation.version,
                )
            case "minikube":
                return self._install_in_cluster(
                    runtime="kind",
                    cluster_id=settings.name,
                    kubeconfig=get_kubeconfig_location(runtime.name),
                    version=cluster_installation.version,
                )
            case _:
                raise ValueError(
                    f"Liqo post CNI install is not supported for runtime: {settings.runtime}"
                )

    def after_all_operations(
        self,
        all_settings: Dict[str, Settings],
        all_runtimes: Dict[str, Type[BaseRuntime]],
        all_cnis: Dict[str, Type[BaseCNI]],
    ) -> None:
        # Runs ONCE in total, after Liqo is installed on every cluster:
        # 1. peers each pair in `peerings` (liqoctl peer)
        # 2. offloads the namespaces listed in each installation's `offloadings`
        #    (liqoctl offload namespace), creating them if missing.
        # Workloads declared in the config are created only after this hook.
        for peering in self.spec.peerings:
            cluster_a = all_settings[peering[0]]
            cluster_b = all_settings[peering[1]]

            # TODO: improve support for different runtimes: replace the check on "k3d" with a property on the runtime
            self._peer_clusters(
                kubeconfig=get_kubeconfig_location(cluster_a.name),
                remote_kubeconfig=get_kubeconfig_location(cluster_b.name),
                gw_server_service_type="LoadBalancer"
                if cluster_b.runtime == "k3d"
                else "NodePort",
            )

        for installation in self.spec.installations:
            for namespace in installation.offloadings:
                self._offload_namespace(
                    kubeconfig=get_kubeconfig_location(installation.cluster),
                    namespace=namespace,
                )

    def _install_in_cluster(
        self,
        cluster_id: str,
        kubeconfig: str,
        version: str,
        runtime: str | None = None,
        api_server_url: str | None = None,
        pod_cidr: str | None = None,
        service_cidr: str | None = None,
    ) -> None:
        print(f"Installing Liqo version {version}")

        repo_url = None
        version_hash = None
        if version is not None and version != "latest":
            (repo_url, version_hash) = version.split("@")

        command = [
            "liqoctl",
            "install",
        ]

        if runtime:
            command.append(runtime)

        command.append("-v")

        # Build installation command by adding parameters
        parameters = {
            "--cluster-id": cluster_id,
            "--pod-cidr": pod_cidr,
            "--service-cidr": service_cidr,
            "--kubeconfig": kubeconfig,
            "--api-server-url": api_server_url,
            "--repo-url": repo_url,
            "--version": version_hash,
        }

        for param, value in parameters.items():
            if value is not None:
                command.extend([param, value])

        max_retries = 10
        for attempt in range(max_retries):
            try:
                print(f"Attempt {attempt + 1} of {max_retries}")
                print(f"Running command: {' '.join(command)}")
                subprocess.run(command, check=True)
                break
            except subprocess.CalledProcessError:
                if attempt == max_retries - 1:
                    raise
                print(f"Attempt {attempt + 1} failed, retrying...")

    def _peer_clusters(
        self,
        kubeconfig: str,
        remote_kubeconfig: str,
        gw_server_service_type: str,
    ) -> None:
        print("Peering clusters")

        command = [
            "liqoctl",
            "peer",
        ]

        # Build installation command by adding parameters
        parameters = {
            "--kubeconfig": kubeconfig,
            "--remote-kubeconfig": remote_kubeconfig,
            "--gw-server-service-type": gw_server_service_type,
        }

        for param, value in parameters.items():
            if value is not None:
                command.extend([param, value])

        # Execute peering command
        subprocess.run(command, check=True)

    def _offload_namespace(self, kubeconfig: str, namespace: str) -> None:
        # Create the namespace if it does not exist
        ensure_kubernetes_namespace(kubeconfig, namespace)

        # Run liqoctl offload namespace command
        command = [
            "liqoctl",
            "offload",
            "namespace",
            namespace,
            "--kubeconfig",
            kubeconfig,
        ]
        subprocess.run(command, check=True)


def _calico_skip_liqo_interfaces(resources: list[dict]) -> None:
    installation = find_object(resources, "Installation", "default")
    installation["spec"]["calicoNetwork"]["nodeAddressAutodetectionV4"] = {
        "skipInterface": "liqo.*"
    }


module = LiqoTool
spec = LiqoToolSpec
