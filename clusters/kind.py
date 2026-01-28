import yaml
import subprocess

from clusters.base import Cluster
from config import CNIEnum


class Kind(Cluster):
    IMAGE = "kindest/node:v1.30.0"  # TODO custom image

    def cleanup(self) -> None:
        subprocess.run(
            ["kind", "delete", "cluster", "--name", self.name],
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
                self.get_kubeconfig_location(),
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

    def install_cni(self) -> None:
        if self.cni != CNIEnum.kindnet:
            raise ValueError("Only 'kindnet' CNI is supported for kind clusters")

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
                    f"curl {self.proxy_address}/setup/systemd | sed s/docker\.service/containerd\.service/g | sed '/Environment/ s/$/ \"NO_PROXY=127.0.0.0\/8,10.0.0.0\/8,172.16.0.0\/12,192.168.0.0\/16\"/' | bash",
                ],
                check=True,
            )

    def _gen_config(self) -> dict:
        return {
            "apiVersion": "kind.x-k8s.io/v1alpha4",
            "kind": "Cluster",
            "networking": {
                "podSubnet": self.cluster_cidr,
                "serviceSubnet": self.service_cidr,
            },
            "nodes": [
                {
                    "role": "control-plane",
                    "image": self.IMAGE,
                    "labels": {"tier": "worker-0"},
                },
                *[
                    {
                        "role": "worker",
                        "image": self.IMAGE,
                        "labels": {"tier": f"worker-{i}"},
                    }
                    for i in range(1, self.nodes)
                ],
            ],
        }
