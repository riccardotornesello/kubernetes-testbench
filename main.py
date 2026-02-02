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

    for cluster in clusters.values():
        if cluster["runtime"].settings.cache:
            cluster["runtime"].set_proxy(proxy_address)

        for tool in tools:
            tool.pre_cluster_init()

        cluster["runtime"].init_cluster()
        for tool in tools:
            tool.post_cluster_init()

        cluster["runtime"].install_cni(cluster["cni"])

        for tool in tools:
            tool.post_cni_install(
                settings=cluster["runtime"].settings,
                runtime=cluster["runtime"],
                cni=cluster["cni"],
            )

    for tool in tools:
        tool.after_all_operations(
            all_settings=all_settings,
            all_runtimes=all_runtimes,
            all_cnis=all_cnis,
        )

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
