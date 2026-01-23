"""
k3d Cluster Implementation

Provides cluster creation and management using k3d (k3s in Docker).
k3d is a lightweight wrapper to run k3s (Rancher Lab's minimal Kubernetes distribution)
in Docker containers, making it perfect for local development and testing.
"""
import yaml
import os
import subprocess
from kubernetes import config, client

from clusters.base import Cluster
from cni.base import CNI
from cni.calico import Calico
from cni.cilium import Cilium
from config import CNIEnum
from const import DOCKER_NETWORK_NAME


class K3d(Cluster):
    """
    k3d cluster implementation.
    
    Creates and manages Kubernetes clusters using k3d, which runs k3s
    (lightweight Kubernetes) in Docker containers. Supports custom CNI
    plugins and multi-node clusters.
    """
    # k3s version to use for cluster creation
    IMAGE = "docker.io/rancher/k3s:v1.30.2-k3s2"  # TODO: make configurable

    def init_cluster(self) -> None:
        """
        Initialize a k3d cluster with the configured settings.
        
        This method:
        1. Generates the k3d configuration
        2. Creates the cluster using k3d CLI
        3. Retrieves and saves the kubeconfig
        
        For non-flannel CNIs, it disables the default flannel to avoid conflicts.
        """
        config = self._gen_config()
        config_yaml = yaml.dump(config)

        additional_args = []

        # Disable default flannel if using a different CNI
        if self.cni != CNIEnum.flannel:
            additional_args.extend(
                [
                    "--k3s-arg",
                    "--flannel-backend=none@server:*",
                    "--k3s-arg",
                    "--disable-network-policy@server:*",
                ]
            )

        # Create the cluster using k3d CLI
        subprocess.run(
            [
                "k3d",
                "cluster",
                "create",
                self.name,
                "--config",
                "-",
                "--kubeconfig-update-default=false",
            ]
            + additional_args,
            input=config_yaml.encode(),
            check=True,
        )

        # Save kubeconfig content to a file for later use
        kubeconfig_content = self._get_kubeconfig_content()
        kubeconfig_location = self.get_kubeconfig_location()

        os.makedirs(os.path.dirname(kubeconfig_location), exist_ok=True)

        with open(kubeconfig_location, "w") as f:
            f.write(kubeconfig_content)

    def install_cni(self) -> None:
        """
        Install the configured CNI plugin in the cluster.
        
        Supports:
        - Calico: Full-featured CNI with network policies
        - Cilium: eBPF-based networking and security
        - Flannel: k3d's default, no installation needed
        """
        kubeconfig_location = self.get_kubeconfig_location()

        # Install the selected CNI plugin
        cni: CNI | None = None

        match self.cni:
            case CNIEnum.calico:
                cni = Calico(kubeconfig=kubeconfig_location, cidr=self.cluster_cidr)
            case CNIEnum.cilium:
                cni = Cilium(kubeconfig=kubeconfig_location, cidr=self.cluster_cidr)
            case CNIEnum.flannel:
                # Skip installation as flannel is default
                pass
            case _:
                raise ValueError(f"Unsupported CNI: {self.cni}")

        if cni is not None:
            cni.install()

    def _get_kubeconfig_content(self) -> str:
        """
        Retrieve the kubeconfig for this cluster from k3d.
        
        Returns:
            Kubeconfig content as a string
        """
        result = subprocess.run(
            ["k3d", "kubeconfig", "get", self.name],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def _gen_config(self) -> dict:
        """
        Generate k3d cluster configuration.
        
        Creates a configuration dictionary for k3d that includes:
        - Server and agent node counts
        - Network settings (CIDR ranges)
        - Node labels for worker identification
        - Docker network attachment
        
        Returns:
            Configuration dictionary in k3d format
        """
        return {
            "apiVersion": "k3d.io/v1alpha5",
            "kind": "Simple",
            "image": self.IMAGE,
            "servers": 1,  # Always 1 control plane node
            "agents": self.nodes - 1,  # Remaining nodes are workers
            "network": DOCKER_NETWORK_NAME,
            "options": {
                "k3s": {
                    "extraArgs": [
                        {
                            "arg": f"--cluster-cidr={self.cluster_cidr}",
                            "nodeFilters": ["server:*"],
                        },
                        {
                            "arg": f"--service-cidr={self.service_cidr}",
                            "nodeFilters": ["server:*"],
                        },
                    ],
                    # Label nodes for identification (useful for node selectors)
                    "nodeLabels": [
                        {
                            "label": "tier=worker-0",
                            "nodeFilters": ["server:0"],
                        },
                        *[
                            {
                                "label": f"tier=worker-{i}",
                                "nodeFilters": [f"agent:{i - 1}"],
                            }
                            for i in range(1, self.nodes)
                        ],
                    ],
                }
            },
        }

    def get_api_server_address(self) -> str:
        """
        Get the internal IP address of the API server.
        
        This is useful for tools like Liqo that need to know the API server
        endpoint for cluster peering.
        
        Returns:
            Internal IP address of the API server
            
        Raises:
            RuntimeError: If API server address cannot be found
        """
        kubeconfig_location = self.get_kubeconfig_location()
        k8s_client = config.new_client_from_config(config_file=kubeconfig_location)
        v1 = client.CoreV1Api(k8s_client)

        # Find the control plane node
        label_selector = "node-role.kubernetes.io/master"

        nodes = v1.list_node(label_selector=label_selector)
        for node in nodes.items:
            for addr in node.status.addresses:
                if addr.type == "InternalIP":
                    return addr.address

        raise RuntimeError("API server address not found")
