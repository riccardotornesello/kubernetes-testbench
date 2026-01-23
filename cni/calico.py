"""
Calico CNI Implementation

Provides Calico CNI installation and configuration for Kubernetes clusters.
Calico is a popular CNI plugin that provides networking and network security
for containers, with support for network policies and multiple data planes.
"""
import tempfile
import requests
from kubernetes import utils, config, client

from cni.base import CNI


class Calico(CNI):
    """
    Calico CNI implementation.
    
    Installs Project Calico using the Tigera operator method. This approach
    uses the operator pattern for managing Calico lifecycle and configuration.
    
    Features:
    - VXLAN encapsulation for pod networking
    - Network policy support
    - Integration with Liqo (skips liqo.* interfaces)
    """
    version: str

    def __init__(self, version: str = "3.30.3", **kwargs) -> None:
        """
        Initialize Calico CNI installer.
        
        Args:
            version: Calico version to install (default: "3.30.3")
            **kwargs: Additional arguments passed to parent CNI class
        """
        super().__init__(**kwargs)
        self.version = version

    def install(self) -> None:
        """
        Install Calico CNI using the Tigera operator.
        
        Installation steps:
        1. Apply operator CRDs (Custom Resource Definitions)
        2. Deploy the Tigera operator
        3. Create Installation, APIServer, Goldmane, and Whisker resources
        
        The operator then handles the actual Calico deployment.
        """
        k8s_client = config.new_client_from_config(config_file=self.kubeconfig)

        # Save manifests to temp files and apply them
        for url in [
            f"https://raw.githubusercontent.com/projectcalico/calico/v{self.version}/manifests/operator-crds.yaml",
            f"https://raw.githubusercontent.com/projectcalico/calico/v{self.version}/manifests/tigera-operator.yaml",
        ]:
            with tempfile.NamedTemporaryFile() as temp_crds:
                response = requests.get(url)
                response.raise_for_status()

                temp_crds.write(response.content)
                temp_crds.flush()

                utils.create_from_yaml(k8s_client, temp_crds.name)

        # Apply Calico installation configuration
        custom_objects_api = client.CustomObjectsApi(k8s_client)
        for resource in self._gen_config():
            custom_objects_api.create_cluster_custom_object(
                group=resource["apiVersion"].split("/")[0],
                version=resource["apiVersion"].split("/")[1],
                plural=resource["kind"].lower() + "s",
                body=resource,
            )

    def _gen_config(self) -> list[dict]:
        """
        Generate Calico configuration resources.
        
        Creates custom resources that configure Calico:
        - Installation: Main Calico configuration with IP pool and encapsulation
        - APIServer: Enables the Calico API server
        - Goldmane: Calico logging and diagnostics
        - Whisker: Calico metrics and monitoring
        
        Returns:
            List of Kubernetes custom resource dictionaries
        """
        return [
            {
                "apiVersion": "operator.tigera.io/v1",
                "kind": "Installation",
                "metadata": {"name": "default"},
                "spec": {
                    "calicoNetwork": {
                        # Skip Liqo interfaces to avoid conflicts
                        "nodeAddressAutodetectionV4": {"skipInterface": "liqo.*"},
                        "ipPools": [
                            {
                                "name": "default-ipv4-ippool",
                                "blockSize": 26,
                                "cidr": self.cidr,
                                "encapsulation": "VXLAN",
                                "natOutgoing": "Enabled",
                                "nodeSelector": "all()",
                            }
                        ],
                    }
                },
            },
            {
                "apiVersion": "operator.tigera.io/v1",
                "kind": "APIServer",
                "metadata": {"name": "default"},
                "spec": {},
            },
            {
                "apiVersion": "operator.tigera.io/v1",
                "kind": "Goldmane",
                "metadata": {"name": "default"},
            },
            {
                "apiVersion": "operator.tigera.io/v1",
                "kind": "Whisker",
                "metadata": {"name": "default"},
            },
        ]
