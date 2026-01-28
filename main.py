import sys
from typing import List, Dict

from config import validate_config_file, ClusterConfig, RuntimeEnum
from clusters.base import Cluster
from clusters.k3d import K3d
from clusters.kind import Kind
from tools.liqo import LiqoTool
from const import DOCKER_NETWORK_NAME
from utils.kubernetes_utils import create_kubernetes_namespace, create_deployment
from utils.docker_utils import ensure_docker_network
from utils.cache import run_registry_proxy_container


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

    # Create Docker network
    ensure_docker_network(DOCKER_NETWORK_NAME)

    # Setup cache if enabled
    proxy_ip = None
    if cfg.cache.enabled:
        print("Setting up registry proxy cache...")
        proxy_ip = run_registry_proxy_container()
        print("Registry proxy cache set up successfully.")

        for cluster in clusters.values():
            cluster.set_proxy(proxy_ip)

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
