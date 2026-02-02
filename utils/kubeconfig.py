def get_kubeconfig_location(cluster_name: str) -> str:
    # TODO: dynamic out directory
    return f"out/kubeconfigs/{cluster_name}.yaml"
