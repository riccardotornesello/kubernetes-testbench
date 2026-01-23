import subprocess
from typing import List

from config import validate_config_file, ClusterConfig, RuntimeEnum
from clusters.base import Cluster
from clusters.k3d import K3d
from clusters.kind import Kind
from tools.liqo import LiqoTool
from const import DOCKER_NETWORK_NAME


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


def parse(cluster_configs: List[ClusterConfig]) -> List[Cluster]:
    cls: List[Cluster] = []

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

        cls.append(cluster)

    return cls


def main() -> None:
    # Create Docker network
    create_docker_network(DOCKER_NETWORK_NAME)

    # Fetch configuration
    cfg = validate_config_file("examples/base.yaml")
    if cfg is None:
        exit(1)

    # Create clusters
    clusters = parse(cfg.clusters)
    for cluster in clusters:
        print(f"Creating cluster: {cluster.name}")
        cluster.create()
        print(f"Cluster {cluster.name} created successfully.")

    # Install tools
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
