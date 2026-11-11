"""Constants shared by the runtimes and the tools."""

# Address pool of the testbench networks (default of `network.cidr`): a /24
# for the shared infrastructure, then one /24 per cluster
DEFAULT_NETWORK_POOL = "10.250.0.0/16"

# Docker network of the shared infrastructure (e.g. the registry proxy). The
# underscore cannot appear in a cluster name: no clash with a cluster network.
INFRA_NETWORK_NAME = "testbench_infra"


def cluster_network_name(cluster: str) -> str:
    """Name of the docker or libvirt network of the nodes of `cluster`."""
    return f"testbench-{cluster}"
