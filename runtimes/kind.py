import yaml
import subprocess

from pydantic import BaseModel
from kubernetes import client, config

from runtimes.base import BaseRuntime
from utils.kubeconfig import get_kubeconfig_location


class KindClusterSpec(BaseModel):
    image: str = "kindest/node:v1.30.0"


class KindCluster(BaseRuntime[KindClusterSpec]):
    SUPPORTED_CNIS = ["kindnet"]
    DEFAULT_CNI = "kindnet"

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
                "--wait",
                "5m",
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
        k8s_client = config.new_client_from_config(config_file=kubeconfig_location)
        v1 = client.CoreV1Api(k8s_client)
        cluster_info = v1.get_code().to_dict()
        return cluster_info["serverAddress"]

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
