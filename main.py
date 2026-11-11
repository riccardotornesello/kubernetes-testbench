from core.input import validate_config_file, ClusterConfig, ToolConfig
from core.modules import runtimes, cnis, tools, runtime_specs, tool_specs
from core.types import Settings
from core.namespaces import create_namespace
from utils.kubeconfig import get_kubeconfig_location
from core.cache import run_registry_proxy_container


# TODO: single function to create settings


def create_runtime_instance(config: ClusterConfig):
    return runtimes[config.runtime.type.value](
        name=config.name,
        settings=Settings(
            runtime=config.runtime.type.value,
            cni=config.cni.value,
            name=config.name,
            nodes=config.nodes,
            cluster_cidr=config.cluster_cidr,
            service_cidr=config.service_cidr,
            cache=config.cache,
        ),
        spec=runtime_specs[config.runtime.type.value](**config.runtime.spec),
    )


def create_cni_instance(config: ClusterConfig):
    return cnis[config.cni.value](
        cluster_name=config.name,
        settings=Settings(
            runtime=config.runtime.type.value,
            cni=config.cni.value,
            name=config.name,
            nodes=config.nodes,
            cluster_cidr=config.cluster_cidr,
            service_cidr=config.service_cidr,
            cache=config.cache,
        ),
    )


def create_tool_instance(tool_config: ToolConfig):
    return tools[tool_config.type](
        spec=tool_specs[tool_config.type](**tool_config.spec),
    )


def main(config_file: str) -> None:
    # Fetch configuration
    cfg = validate_config_file(config_file)
    if cfg is None:
        exit(1)

    tools = [create_tool_instance(tool_cfg) for tool_cfg in cfg.tools]
    clusters = {
        cluster_cfg.name: {
            "runtime": create_runtime_instance(cluster_cfg),
            "cni": create_cni_instance(cluster_cfg),
        }
        for cluster_cfg in cfg.clusters
    }

    all_settings = {cluster_cfg.name: cluster_cfg for cluster_cfg in cfg.clusters}
    all_runtimes = {cluster: clusters[cluster]["runtime"] for cluster in clusters}
    all_cnis = {cluster: clusters[cluster]["cni"] for cluster in clusters}

    for cluster in clusters.values():
        cluster["runtime"].check_dependencies()
        cluster["cni"].check_dependencies()

    for cluster in clusters.values():
        cluster["runtime"].cleanup()

    proxy_address = run_registry_proxy_container()  # TODO: only if needed

    # TODO: create network

    # Per-cluster phase: clusters are created sequentially, and every tool hook
    # inside this loop runs once PER CLUSTER (for every tool).
    for cluster in clusters.values():
        if cluster["runtime"].settings.cache:
            cluster["runtime"].set_proxy(proxy_address)

        hook_args = {
            "settings": cluster["runtime"].settings,
            "runtime": cluster["runtime"],
            "cni": cluster["cni"],
        }

        # Hook: once per cluster, before cluster creation
        for tool in tools:
            tool.pre_cluster_init(**hook_args)

        cluster["runtime"].init_cluster()

        # Hook: once per cluster, after cluster creation, before CNI
        for tool in tools:
            tool.post_cluster_init(**hook_args)

        # Hook: once per cluster, before CNI installation
        for tool in tools:
            tool.pre_cni_install(**hook_args)

        cluster["runtime"].install_cni(cluster["cni"])

        # Hook: once per cluster, after CNI installation
        for tool in tools:
            tool.post_cni_install(**hook_args)

    # Hook: ONCE in total, after all clusters are ready (cross-cluster ops)
    for tool in tools:
        tool.after_all_operations(
            all_settings=all_settings,
            all_runtimes=all_runtimes,
            all_cnis=all_cnis,
        )

    # Workloads are created last, after every tool hook (no tool hook runs here)
    for cluster_cfg in cfg.clusters:
        kubeconfig_path = get_kubeconfig_location(cluster_cfg.name)
        for namespace_cfg in cluster_cfg.namespaces:
            create_namespace(kubeconfig_path, namespace_cfg)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python main.py <config_file>")
        exit(1)
    main(sys.argv[1])
