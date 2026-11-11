"""minikube runtime: VMs (kvm2 driver) or docker containers."""

import os
import shlex
import subprocess
from typing import Literal, Optional

from pydantic import Field

from runtimes.base import (
    BaseRuntime,
    BaseRuntimeSpec,
    CNI_PLUGINS_VERSION,
    NODE_TIER_LABEL,
    node_tier,
)
from core.log import get_logger
from core.settings import DOCKER_NETWORK_NAME
from utils.commands import run_command
from utils.docker_utils import ensure_kernel_modules, install_cni_plugins
from utils.kubeconfig import get_kubeconfig_location
from utils.kubernetes_utils import label_nodes

log = get_logger(__name__)


# API server port inside the minikube nodes
API_SERVER_PORT = 8443

# systemd service pulling the images, for each container runtime
CONTAINER_RUNTIME_SERVICES = {
    "containerd": "containerd",
    "cri-o": "crio",
    "docker": "docker",
}

# Network shared by the clusters of the testbench, for each driver: with kvm2
# it is a libvirt network (created by minikube if missing), with docker the
# network shared with the kind/k3d clusters and the registry proxy.
DEFAULT_NETWORKS = {
    "kvm2": "testbenchnet",
    "docker": DOCKER_NETWORK_NAME,
}


class MinikubeClusterSpec(BaseRuntimeSpec):
    # Kubernetes version (without the leading "v")
    kubernetes_version: str = "1.30.0"
    # kvm2: one VM per node. docker: one container per node.
    driver: Literal["kvm2", "docker"] = "kvm2"
    container_runtime: Literal["containerd", "cri-o", "docker"] = "containerd"
    # Network the nodes are attached to (default: depends on the driver, see
    # DEFAULT_NETWORKS). Clusters must share it to reach each other.
    network: Optional[str] = None
    # Resources of each node (default: minikube defaults)
    cpus: Optional[int] = Field(default=None, ge=2)
    memory: Optional[str] = None  # e.g. "4g", "4096mb"
    disk_size: Optional[str] = None  # e.g. "20g"


class MinikubeCluster(BaseRuntime[MinikubeClusterSpec]):
    """Cluster created with minikube, one profile per cluster."""

    @property
    def _kubeconfig(self) -> str:
        """Kubeconfig of the cluster."""
        return get_kubeconfig_location(self.name)

    def _run(
        self, args: list[str], capture: bool = False
    ) -> subprocess.CompletedProcess[str]:
        # minikube reads/writes the kubeconfig at $KUBECONFIG: point it to the
        # kubeconfig of this cluster only, never to ~/.kube/config
        env = {**os.environ, "KUBECONFIG": self._kubeconfig}
        return run_command(
            ["minikube", *args, "-p", self.name], env=env, capture=capture
        )

    @property
    def on_docker_network(self) -> bool:
        network = self.spec.network or DEFAULT_NETWORKS[self.spec.driver]
        return self.spec.driver == "docker" and network == DOCKER_NETWORK_NAME

    def check_dependencies(self):
        run_command(["minikube", "version"], capture=True)

    def init_cluster(self) -> None:
        spec = self.spec
        args = [
            "start",
            "--nodes",
            str(self.settings.nodes),
            "--kubernetes-version",
            f"v{spec.kubernetes_version}",
            "--driver",
            spec.driver,
            "--container-runtime",
            spec.container_runtime,
            "--network",
            spec.network or DEFAULT_NETWORKS[spec.driver],
            # The CNI is always installed by the testbench: the minikube
            # built-in manifests ignore the CNI spec (e.g. flannel always uses
            # 10.244.0.0/16, whatever the cluster CIDR is)
            "--cni",
            "false",
            "--service-cluster-ip-range",
            self.settings.service_cidr,
            "--extra-config",
            f"kubeadm.pod-network-cidr={self.settings.cluster_cidr}",
            # Self-contained kubeconfig (no paths to ~/.minikube)
            "--embed-certs",
            # Nodes and system pods become Ready only after the CNI is installed
            "--wait",
            "apiserver",
        ]

        if self.cni.replaces_kube_proxy:
            args.extend(["--extra-config", "kubeadm.skip-phases=addon/kube-proxy"])

        for flag, value in (
            ("--cpus", spec.cpus),
            ("--memory", spec.memory),
            ("--disk-size", spec.disk_size),
        ):
            if value is not None:
                args.extend([flag, str(value)])

        os.makedirs(os.path.dirname(self._kubeconfig), exist_ok=True)
        self._run(args)

        # minikube has no option to label every node differently
        label_nodes(
            self._kubeconfig,
            {
                node: {NODE_TIER_LABEL: node_tier(i)}
                for i, node in enumerate(self._get_nodes())
            },
        )

        if self.node_proxy is not None:
            self._configure_node_proxy()

    def cleanup(self) -> None:
        self._run(["delete"])

    def get_api_server_address(self) -> str:
        result = self._run(["ip"], capture=True)
        return f"https://{result.stdout.strip()}:{API_SERVER_PORT}"

    def _get_nodes(self) -> list[str]:
        # Control plane first, then the workers (<name>-m02, <name>-m03, ...)
        result = self._run(["node", "list"], capture=True)
        return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]

    def load_kernel_modules(self, modules: list[str]) -> None:
        # VM nodes already load them. Container nodes share the host kernel:
        # loading on one is enough.
        if self.spec.driver == "docker":
            ensure_kernel_modules(self._get_nodes()[0], modules)

    def install_cni_plugins(self, plugins: list[str]) -> None:
        # The minikube ISO ships the reference CNI plugins. Only the missing
        # ones are copied in the container nodes.
        if self.spec.driver == "docker":
            install_cni_plugins(self._get_nodes(), plugins, CNI_PLUGINS_VERSION)

    def _configure_node_proxy(self) -> None:
        """Makes the container runtime of every node pull through the node proxy."""
        script = self.node_proxy_setup_script(
            service=CONTAINER_RUNTIME_SERVICES[self.spec.container_runtime]
        )
        for node in self._get_nodes():
            self._run(["ssh", "-n", node, "--", f"sudo sh -c {shlex.quote(script)}"])


module = MinikubeCluster
spec = MinikubeClusterSpec
