import yaml
import subprocess

from pydantic import BaseModel

from runtimes.base import BaseRuntime, CNI_PLUGINS_VERSION
from utils.docker_utils import ensure_kernel_modules, install_cni_plugins
from utils.kubeconfig import get_kubeconfig_location


class KindClusterSpec(BaseModel):
    image: str = "kindest/node:v1.30.0"


class KindCluster(BaseRuntime[KindClusterSpec]):
    def check_dependencies(self):
        subprocess.run(
            ["kind", "--version"],
            check=True,
        )

    def init_cluster(self) -> None:
        cluster_config = self._gen_config()
        cluster_config_yaml = yaml.dump(cluster_config)

        # Create the cluster using kind CLI
        subprocess.run(
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
            input=cluster_config_yaml.encode(),
            check=True,
        )

        if self.proxy_address is not None:
            self._install_cache_proxy()

    def cleanup(self) -> None:
        subprocess.run(
            ["kind", "delete", "cluster", "--name", self.name],
            check=True,
        )

    def get_api_server_address(self) -> str:
        kubeconfig_location = get_kubeconfig_location(self.name)
        with open(kubeconfig_location) as f:
            kubeconfig_content = yaml.safe_load(f)
        return kubeconfig_content["clusters"][0]["cluster"]["server"]

    def _get_nodes(self) -> list[str]:
        result = subprocess.run(
            [
                "kind",
                "get",
                "nodes",
                "--name",
                self.name,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        nodes = result.stdout.strip().splitlines()
        return nodes

    def load_kernel_modules(self, modules: list[str]) -> None:
        # Nodes are containers sharing the host kernel: loading on one is enough
        ensure_kernel_modules(self._get_nodes()[0], modules)

    def install_cni_plugins(self, plugins: list[str]) -> None:
        # kindest/node ships only host-local, loopback, portmap and ptp
        install_cni_plugins(self._get_nodes(), plugins, CNI_PLUGINS_VERSION)

    def _install_cache_proxy(self) -> None:
        for node in self._get_nodes():
            subprocess.run(
                [
                    "docker",
                    "exec",
                    node,
                    "sh",
                    "-c",
                    f"curl {self.proxy_address}/setup/systemd | sed s/docker\\.service/containerd\\.service/g | sed '/Environment/ s/$/ \"NO_PROXY=ttl.sh,storage.googleapis.com,127.0.0.0\\/8,10.0.0.0\\/8,172.16.0.0\\/12,192.168.0.0\\/16\"/' | bash",
                ],
                check=True,
            )

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
                    "role": "control-plane",
                    "image": self.spec.image,
                    "labels": {"tier": "worker-0"},
                },
                *[
                    {
                        "role": "worker",
                        "image": self.spec.image,
                        "labels": {"tier": f"worker-{i}"},
                    }
                    for i in range(1, self.settings.nodes)
                ],
            ],
        }


module = KindCluster
spec = KindClusterSpec
