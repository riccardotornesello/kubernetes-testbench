import subprocess
import yaml

from cni.base import CNI


class Cilium(CNI):
    version: str

    def __init__(self, version: str = "1.18.6", **kwargs) -> None:
        super().__init__(**kwargs)
        self.version = version

    def install(self) -> None:
        command = [
            "cilium",
            "install",
            "--kubeconfig",
            self.kubeconfig,
            "--version",
            self.version,
            "--values",
            "-",
        ]
        subprocess.run(
            command,
            input=yaml.dump(self._gen_config()).encode(),
            check=True,
        )

    def _gen_config(self) -> list[dict]:
        return {
            "affinity": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "liqo.io/type",
                                        "operator": "DoesNotExist",
                                    }
                                ]
                            }
                        ]
                    }
                }
            },
            "ipam": {"operator": {"clusterPoolIPv4PodCIDRList": [self.cidr]}},
        }
