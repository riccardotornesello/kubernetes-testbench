"""
Kubernetes Testbench - Main Entry Point

This module serves as the main entry point for the Kubernetes Testbench
tool. It orchestrates the creation of virtual Kubernetes clusters,
installation of CNI plugins, and deployment of additional tools like Liqo.

The workflow is:
1. Create a Docker network for cluster communication
2. Parse and validate the configuration file
3. Create configured clusters with selected runtimes
4. Install CNI plugins in each cluster
5. Install and configure additional tools
"""
import subprocess
from typing import List

from config import validate_config_file, ClusterConfig, RuntimeEnum
from clusters.base import Cluster
from clusters.k3d import K3d
from tools.liqo import LiqoTool
from const import DOCKER_NETWORK_NAME


def create_docker_network(network_name: str) -> None:
    """
    Create a Docker network for cluster communication.
    
    This network allows multiple k3d clusters to communicate with each other,
    which is essential for multi-cluster setups and tools like Liqo.
    
    Args:
        network_name: Name of the Docker network to create
        
    Raises:
        subprocess.CalledProcessError: If network creation fails
    """
    # Check if the Docker network already exists
    exists = (
        subprocess.run(
            ["docker", "network", "inspect", network_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )

    if exists:
        print(f"Docker network '{network_name}' already exists. Skipping.")
        return

    # If not, create it
    print(f"Creating Docker network: {network_name}")
    try:
        subprocess.run(
            ["docker", "network", "create", network_name],
            check=True,
            capture_output=True,  # Capture output to handle errors
            text=True,
        )
        print(f"Network '{network_name}' created successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to create network: {e.stderr}")
        raise e


def parse(cluster_configs: List[ClusterConfig]) -> List[Cluster]:
    """
    Parse cluster configurations and instantiate appropriate cluster objects.
    
    This function takes validated configuration data and creates concrete cluster
    instances based on the specified runtime (e.g., k3d). This design allows for
    easy extension to support additional runtimes in the future.
    
    Args:
        cluster_configs: List of validated cluster configurations
        
    Returns:
        List of instantiated Cluster objects ready for creation
        
    Raises:
        ValueError: If an unsupported runtime is specified
    """
    cls: List[Cluster] = []

    for cfg in cluster_configs:
        cluster: Cluster

        # Match on runtime type and instantiate appropriate cluster class
        match cfg.runtime:
            case RuntimeEnum.k3d:
                cluster = K3d(
                    name=cfg.name,
                    nodes=cfg.nodes,
                    cluster_cidr=cfg.cluster_cidr,
                    service_cidr=cfg.service_cidr,
                    cni=cfg.cni,
                )
            case _:
                raise ValueError(f"Unsupported Runtime: {cfg.runtime}")

        cls.append(cluster)

    return cls


def main() -> None:
    """
    Main execution function for Kubernetes Testbench.
    
    This function orchestrates the entire cluster creation and configuration process:
    1. Creates a shared Docker network
    2. Loads and validates configuration
    3. Creates all specified clusters
    4. Installs configured tools
    """
    # Create Docker network for cluster communication
    create_docker_network(DOCKER_NETWORK_NAME)

    # Fetch and validate configuration from YAML file
    cfg = validate_config_file("examples/base.yaml")
    if cfg is None:
        exit(1)

    # Create clusters based on configuration
    clusters = parse(cfg.clusters)
    for cluster in clusters:
        print(f"Creating cluster: {cluster.name}")
        cluster.create()
        print(f"Cluster {cluster.name} created successfully.")

    # Install tools if configured
    tools = []
    if cfg.tools.liqo:
        tools.append(
            LiqoTool(
                config=cfg.tools.liqo,
                clusters={cluster.name: cluster for cluster in clusters},
            )
        )

    for tool in tools:
        print(f"Installing tool: {tool.__class__.__name__}")
        tool.install()
        print(f"Tool {tool.__class__.__name__} installed successfully.")


if __name__ == "__main__":
    main()
