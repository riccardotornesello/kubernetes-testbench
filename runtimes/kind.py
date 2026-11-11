import yaml

from runtimes.base import (
    BaseRuntime,
    BaseRuntimeSpec,
    CNI_PLUGINS_VERSION,
    NODE_TIER_LABEL,
    node_tier,
)
from core.settings import DOCKER_NETWORK_NAME
from utils.docker_utils import (
    client as docker_client,
    ensure_kernel_modules,
    install_cni_plugins,
)
from utils.kubeconfig import get_kubeconfig_location
from utils.commands import run_command


class KindClusterSpec(BaseRuntimeSpec):
    # Node image: determines the Kubernetes version
    image: str = "kindest/node:v1.30.0"


class KindCluster(BaseRuntime[KindClusterSpec]):
    def check_dependencies(self):
        run_command(["kind", "--version"], capture=True)

    def init_cluster(self) -> None:
        cluster_config = self._gen_config()
        cluster_config_yaml = yaml.dump(cluster_config)

        # Create the cluster using kind CLI
        run_command(
            [
                "kind",
                "create",
                "cluster",
                "--name",
                self.name,
                "--kubeconfig",
                get_kubeconfig_location(self.name),
                "--config",
                "-",
                # No --wait: nodes become Ready only after the CNI is installed
            ],
            input=cluster_config_yaml,
        )

        if self.node_proxy is not None:
            self._configure_node_proxy()

    def cleanup(self) -> None:
        run_command(["kind", "delete", "cluster", "--name", self.name])

    def get_api_server_address(self) -> str:
        # The kubeconfig points to the port forwarded on 127.0.0.1: use the
        # control plane container address on the shared docker network.
        container = docker_client.containers.get(f"{self.name}-control-plane")
        networks = container.attrs["NetworkSettings"]["Networks"]
        return f"https://{networks[DOCKER_NETWORK_NAME]['IPAddress']}:6443"

    def _get_nodes(self) -> list[str]:
        result = run_command(
            [
                "kind",
                "get",
                "nodes",
                "--name",
                self.name,
            ],
            capture=True,
        )
        nodes = result.stdout.strip().splitlines()
        return nodes

    def load_kernel_modules(self, modules: list[str]) -> None:
        # Nodes are containers sharing the host kernel: loading on one is enough
        ensure_kernel_modules(self._get_nodes()[0], modules)

    def install_cni_plugins(self, plugins: list[str]) -> None:
        # kindest/node ships only host-local, loopback, portmap and ptp
        install_cni_plugins(self._get_nodes(), plugins, CNI_PLUGINS_VERSION)

    def _configure_node_proxy(self) -> None:
        script = self.node_proxy_setup_script(service="containerd")
        for node in self._get_nodes():
            run_command(["docker", "exec", node, "sh", "-c", script])

    def _gen_config(self) -> dict:
        return {
            "apiVersion": "kind.x-k8s.io/v1alpha4",
            "kind": "Cluster",
            "networking": {
                "podSubnet": self.settings.cluster_cidr,
                "serviceSubnet": self.settings.service_cidr,
                # The CNI is always installed by the testbench
                "disableDefaultCNI": True,
                "kubeProxyMode": "none" if self.cni.replaces_kube_proxy else "iptables",
            },
            "nodes": [
                {
                    "role": "control-plane" if i == 0 else "worker",
                    "image": self.spec.image,
                    "labels": {NODE_TIER_LABEL: node_tier(i)},
                }
                for i in range(self.settings.nodes)
            ],
        }


module = KindCluster
spec = KindClusterSpec
