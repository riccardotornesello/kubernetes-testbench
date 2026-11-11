from core.paths import output_path


def get_kubeconfig_location(cluster_name: str) -> str:
    """Absolute path of the kubeconfig of the cluster (only this cluster)."""
    return output_path("kubeconfigs", f"{cluster_name}.yaml")
