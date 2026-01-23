"""
Cilium CNI Implementation

Provides Cilium CNI installation and configuration for Kubernetes clusters.
Cilium is an eBPF-based networking and security solution that provides
advanced features like transparent encryption, L7 network policies, and
observability without the overhead of traditional networking approaches.
"""
import subprocess
import yaml

from cni.base import CNI


class Cilium(CNI):
    """
    Cilium CNI implementation.
    
    Installs Cilium using the official Cilium CLI. This provides a streamlined
    installation experience with support for custom configurations.
    
    Features:
    - eBPF-based networking for high performance
    - Advanced network policies (L3/L4 and L7)
    - Integration with Liqo (avoids scheduling on Liqo virtual nodes)
    """
    version: str

    def __init__(self, version: str = "1.18.6", **kwargs) -> None:
        """
        Initialize Cilium CNI installer.
        
        Args:
            version: Cilium version to install (default: "1.18.6")
            **kwargs: Additional arguments passed to parent CNI class
        """
        super().__init__(**kwargs)
        self.version = version

    def install(self) -> None:
        """
        Install Cilium CNI using the Cilium CLI.
        
        Uses the 'cilium install' command with custom values to configure:
        - Node affinity to avoid Liqo virtual nodes
        - IPAM (IP Address Management) with custom pod CIDR
        """
        command = [
            "cilium",
            "install",
            "--kubeconfig",
            self.kubeconfig,
            "--version",
            self.version,
            "--values",
            "-",  # Read values from stdin
        ]
        subprocess.run(
            command,
            input=yaml.dump(self._gen_config()).encode(),
            check=True,
        )

    def _gen_config(self) -> dict:
        """
        Generate Cilium configuration values.
        
        Configures:
        - Node affinity to prevent Cilium from running on Liqo virtual nodes
        - IPAM operator with custom pod CIDR for IP allocation
        
        Returns:
            Configuration dictionary in Helm values format
        """
        return {
            "affinity": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        # Avoid Liqo virtual nodes
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
                "operator": {
                    # Configure IP allocation for pods
                    "clusterPoolIPv4PodCIDRList": [self.cidr]
                }
            },
        }
