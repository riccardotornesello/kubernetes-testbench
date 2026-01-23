"""
Liqo Tool Integration

Provides Liqo installation and cluster peering functionality.
Liqo is an open-source project that enables dynamic and seamless Kubernetes
multi-cluster topologies, allowing resource sharing and workload offloading
across clusters.

For more information, visit: https://liqo.io/
"""
import subprocess
from typing import Dict

from config import LiqoConfig
from tools.base import Tool
from clusters.k3d import K3d
from clusters.base import Cluster


class LiqoTool(Tool):
    """
    Liqo tool implementation.
    
    Handles Liqo installation across multiple clusters and establishes
    peering relationships between clusters. This enables:
    - Resource sharing between clusters
    - Workload offloading across clusters
    - Unified multi-cluster networking
    
    Attributes:
        config: Liqo configuration including installations and peerings
        clusters: Dictionary mapping cluster names to Cluster objects
    """
    config: LiqoConfig
    clusters: Dict[str, Cluster]

    def __init__(self, config: LiqoConfig, clusters: Dict[str, Cluster]) -> None:
        """
        Initialize Liqo tool installer.
        
        Args:
            config: Liqo configuration from the main config file
            clusters: Dictionary of cluster name to Cluster object mappings
        """
        self.config = config
        self.clusters = clusters

    def install(self) -> None:
        """
        Install Liqo in configured clusters and establish peerings.
        
        Process:
        1. Install Liqo in each specified cluster
        2. Establish peering connections between cluster pairs
        """
        # Install Liqo in each configured cluster
        for installation in self.config.installations:
            cluster = self.clusters[installation.cluster]

            if isinstance(cluster, K3d):
                self._install_in_cluster(
                    runtime="k3s",
                    cluster_id=cluster.name,
                    kubeconfig=cluster.get_kubeconfig_location(),
                    version=installation.version,
                    api_server_url=cluster.get_api_server_address(),
                    pod_cidr=cluster.cluster_cidr,
                    service_cidr=cluster.service_cidr,
                )
            else:
                raise ValueError(
                    f"Liqo installation is not supported for cluster: {cluster.name}"
                )

        # Establish peering connections between clusters
        for peering in self.config.peerings:
            cluster_a = self.clusters[peering[0]]
            cluster_b = self.clusters[peering[1]]

            self._peer_clusters(
                kubeconfig=cluster_a.get_kubeconfig_location(),
                remote_kubeconfig=cluster_b.get_kubeconfig_location(),
                # k3d clusters support LoadBalancer via built-in load balancer
                gw_server_service_type="LoadBalancer"
                if isinstance(cluster_b, K3d)
                else "NodePort",
            )

    def _install_in_cluster(
        self,
        runtime: str,
        cluster_id: str,
        kubeconfig: str,
        version: str,
        api_server_url: str | None = None,
        pod_cidr: str | None = None,
        service_cidr: str | None = None,
    ) -> None:
        """
        Install Liqo in a specific cluster using liqoctl.
        
        Args:
            runtime: Kubernetes runtime (e.g., "k3s", "kind")
            cluster_id: Unique identifier for the cluster
            kubeconfig: Path to cluster's kubeconfig file
            version: Liqo version to install (format: "repo@hash" or "latest")
            api_server_url: URL of the cluster's API server
            pod_cidr: Pod network CIDR
            service_cidr: Service network CIDR
        """
        print(f"Installing Liqo version {version}")

        repo_url = None
        version_hash = None
        # Parse version string if it's not "latest"
        if version is not None and version != "latest":
            (repo_url, version_hash) = version.split("@")

        command = [
            "liqoctl",
            "install",
            runtime,
        ]

        # Build installation command by adding parameters
        parameters = {
            "--cluster-id": cluster_id,
            "--pod-cidr": pod_cidr,
            "--service-cidr": service_cidr,
            "--kubeconfig": kubeconfig,
            "--api-server-url": api_server_url,
            "--repo-url": repo_url,
            "--version": version_hash,
        }

        # Add non-None parameters to command
        for param, value in parameters.items():
            if value is not None:
                command.extend([param, value])

        # Execute installation command
        subprocess.run(command, check=True)

    def _peer_clusters(
        self,
        kubeconfig: str,
        remote_kubeconfig: str,
        gw_server_service_type: str,
    ) -> None:
        """
        Establish a peering connection between two clusters.
        
        Peering enables:
        - Resource discovery across clusters
        - Network connectivity for pod-to-pod communication
        - Workload offloading capabilities
        
        Args:
            kubeconfig: Path to the local cluster's kubeconfig
            remote_kubeconfig: Path to the remote cluster's kubeconfig
            gw_server_service_type: Service type for gateway server
                                    ("LoadBalancer" or "NodePort")
        """
        print("Peering clusters")

        command = [
            "liqoctl",
            "peer",
        ]

        # Build peering command by adding parameters
        parameters = {
            "--kubeconfig": kubeconfig,
            "--remote-kubeconfig": remote_kubeconfig,
            "--gw-server-service-type": gw_server_service_type,
        }

        for param, value in parameters.items():
            if value is not None:
                command.extend([param, value])

        # Execute peering command
        subprocess.run(command, check=True)
