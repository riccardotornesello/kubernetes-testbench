import subprocess
import yaml

from cnis.base import BaseCNI
from utils.kubeconfig import get_kubeconfig_location


class CiliumCNI(BaseCNI):
    VERSION = "1.18.6"  # TODO: move to spec

    def check_dependencies(self) -> None:
        try:
            subprocess.run(
                ["cilium", "version"],
                capture_output=True,
                check=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "Cilium CLI is not installed or not found in PATH."
            ) from e

    def install_cni(self) -> None:
        kubeconfig_location = get_kubeconfig_location(self.cluster_name)

        command = [
            "cilium",
            "install",
            "--kubeconfig",
            kubeconfig_location,
            "--version",
            self.VERSION,
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
            "ipam": {
                "operator": {"clusterPoolIPv4PodCIDRList": [self.settings.cluster_cidr]}
            },
        }


module = CiliumCNI
