import subprocess
import sys
from typing import List, Dict

from config import validate_config_file, ClusterConfig, RuntimeEnum
from clusters.base import Cluster
from clusters.k3d import K3d
from clusters.kind import Kind
from tools.liqo import LiqoTool
from const import DOCKER_NETWORK_NAME
from utils.kubernetes import create_kubernetes_namespace, create_deployment


def delete_docker_network(network_name: str) -> None:
    print(f"Deleting Docker network: {network_name}")
    try:
        subprocess.run(
            ["docker", "network", "rm", network_name],
            check=True,
            capture_output=True,  # Capture output to handle errors
            text=True,
        )
        print(f"Network '{network_name}' deleted successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to delete network: {e.stderr}")
        raise e


def create_docker_network(network_name: str) -> None:
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


def parse_clusters(cluster_configs: List[ClusterConfig]) -> Dict[str, Cluster]:
    cls: Dict[str, Cluster] = {}

    for cfg in cluster_configs:
        cluster: Cluster

        match cfg.runtime:
            case RuntimeEnum.k3d:
                cluster = K3d(
                    name=cfg.name,
                    nodes=cfg.nodes,
                    cluster_cidr=cfg.cluster_cidr,
                    service_cidr=cfg.service_cidr,
                    cni=cfg.cni,
                )
            case RuntimeEnum.kind:
                cluster = Kind(
                    name=cfg.name,
                    nodes=cfg.nodes,
                    cluster_cidr=cfg.cluster_cidr,
                    service_cidr=cfg.service_cidr,
                    cni=cfg.cni,
                )
            case _:
                raise ValueError(f"Unsupported Runtime: {cfg.runtime}")

        cls[cfg.name] = cluster

    return cls


def main(config_file: str) -> None:
    # Fetch configuration
    cfg = validate_config_file(config_file)
    if cfg is None:
        exit(1)

    clusters = parse_clusters(cfg.clusters)

    # Cleanup
    for cluster in clusters.values():
        print(f"Cleaning up cluster: {cluster.name}")
        cluster.cleanup()
        print(f"Cluster {cluster.name} cleaned up successfully.")

    delete_docker_network(DOCKER_NETWORK_NAME)

    # Create Docker network
    create_docker_network(DOCKER_NETWORK_NAME)

    # Create clusters
    for cluster in clusters.values():
        print(f"Creating cluster: {cluster.name}")
        cluster.create()
        print(f"Cluster {cluster.name} created successfully.")

    # Create deployments
    for cluster in cfg.clusters:
        for namespace in cluster.namespaces:
            print(f"Creating namespace: {namespace.name} in cluster: {cluster.name}")
            create_kubernetes_namespace(
                kubeconfig=clusters[cluster.name].get_kubeconfig_location(),
                namespace_name=namespace.name,
            )

            for deployment in namespace.deployments:
                print(
                    f"Creating deployment: {deployment.name} in namespace: {namespace.name} of cluster: {cluster.name}"
                )
                create_deployment(
                    kubeconfig_path=clusters[cluster.name].get_kubeconfig_location(),
                    deployment_name=deployment.name,
                    namespace=namespace.name,
                    pod_spec=deployment.pod_spec,
                    replicas=deployment.replicas,
                )

    # Install tools
    tools = []
    if cfg.tools.liqo:
        tools.append(
            LiqoTool(
                config=cfg.tools.liqo,
                clusters=clusters,
            )
        )

    for tool in tools:
        print(f"Installing tool: {tool.__class__.__name__}")
        tool.install()
        print(f"Tool {tool.__class__.__name__} installed successfully.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python main.py <config_file_path>")
        sys.exit(1)

    config_path = sys.argv[1]
    main(config_path)
