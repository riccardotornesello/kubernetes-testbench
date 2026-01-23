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

    def install_cni(self) -> None:
        if self.cni != CNIEnum.kindnet:
            raise ValueError("Only 'kindnet' CNI is supported for kind clusters")

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
