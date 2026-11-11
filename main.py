import argparse
import sys

from core.input import validate_config_file, ClusterConfig
from core.log import get_logger, setup_logging
from core.paths import DEFAULT_OUTPUT_DIR, set_output_dir
from core.settings import DOCKER_NETWORK_NAME
from core.modules import runtimes, cnis, tools as tool_modules
from core.types import Settings
from core.namespaces import create_namespace
from utils.docker_utils import ensure_docker_network
from utils.kubeconfig import get_kubeconfig_location

log = get_logger("testbench")


def create_settings(config: ClusterConfig) -> Settings:
    return Settings(
        runtime=config.runtime.type,
        cni=config.cni.type,
        name=config.name,
        nodes=config.nodes,
        cluster_cidr=config.cluster_cidr,
        service_cidr=config.service_cidr,
    )


def create_cni_instance(config: ClusterConfig):
    return cnis[config.cni.type](
        cluster_name=config.name,
        settings=create_settings(config),
        spec=config.cni.spec,
    )


def create_runtime_instance(config: ClusterConfig, cni):
    return runtimes[config.runtime.type](
        name=config.name,
        settings=create_settings(config),
        spec=config.runtime.spec,
        cni=cni,
    )


def create_tool_instance(tool_config):
    return tool_modules[tool_config.type](spec=tool_config.spec)


def main(config_file: str) -> None:
    # Fetch configuration
    cfg = validate_config_file(config_file)
    if cfg is None:
        sys.exit(1)

    tools = [create_tool_instance(tool_cfg) for tool_cfg in cfg.tools]
    clusters = {}
    for cluster_cfg in cfg.clusters:
        cni = create_cni_instance(cluster_cfg)
        clusters[cluster_cfg.name] = {
            "runtime": create_runtime_instance(cluster_cfg, cni),
            "cni": cni,
        }

    all_runtimes = {cluster: clusters[cluster]["runtime"] for cluster in clusters}
    all_cnis = {cluster: clusters[cluster]["cni"] for cluster in clusters}
    all_settings = {name: runtime.settings for name, runtime in all_runtimes.items()}

    log.info(
        "Configuration loaded",
        clusters=",".join(clusters),
        tools=",".join(tool_cfg.type for tool_cfg in cfg.tools) or "-",
    )

    log.info("Checking dependencies")
    for cluster in clusters.values():
        cluster["runtime"].check_dependencies()
        cluster["cni"].check_dependencies()
    for tool in tools:
        tool.check_dependencies()

    for name, cluster in clusters.items():
        log.info("Deleting existing cluster", cluster=name)
        cluster["runtime"].cleanup()

    # Shared docker network of the clusters attached to it (kind, k3d,
    # minikube docker driver), created in advance so that every runtime finds
    # it, whatever the cluster order (kind and minikube reuse an existing one)
    if any(runtime.on_docker_network for runtime in all_runtimes.values()):
        log.info("Ensuring docker network", network=DOCKER_NETWORK_NAME)
        ensure_docker_network(DOCKER_NETWORK_NAME)

    # Hook: ONCE in total, before any cluster is created (shared infrastructure)
    for tool in tools:
        tool.before_all_operations(
            all_settings=all_settings,
            all_runtimes=all_runtimes,
            all_cnis=all_cnis,
        )

    # Per-cluster phase: clusters are created sequentially, and every tool hook
    # inside this loop runs once PER CLUSTER (for every tool).
    for name, cluster in clusters.items():
        cluster_log = log.bind(cluster=name)
        hook_args = {
            "settings": cluster["runtime"].settings,
            "runtime": cluster["runtime"],
            "cni": cluster["cni"],
        }

        # Hook: once per cluster, before cluster creation
        for tool in tools:
            tool.pre_cluster_init(**hook_args)

        cluster_log.info(
            "Creating cluster",
            runtime=cluster["runtime"].settings.runtime,
            nodes=cluster["runtime"].settings.nodes,
        )
        cluster["runtime"].init_cluster()

        # Hook: once per cluster, after cluster creation, before CNI
        for tool in tools:
            tool.post_cluster_init(**hook_args)

        # Hook: once per cluster, before CNI installation
        for tool in tools:
            tool.pre_cni_install(**hook_args)

        cluster_log.info("Installing CNI", cni=cluster["cni"].settings.cni)
        cluster["runtime"].install_cni()
        cluster_log.info("Cluster ready")

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
            log.info(
                "Creating namespace workloads",
                cluster=cluster_cfg.name,
                namespace=namespace_cfg.name,
            )
            create_namespace(kubeconfig_path, namespace_cfg)

    log.info("Testbench ready")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the clusters described by a testbench configuration."
    )
    parser.add_argument("config_file", help="YAML configuration file")
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="directory of the generated files: kubeconfigs, caches, "
        "registry proxy data (default: %(default)s)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="minimum level of the logs (default: %(default)s)",
    )
    parser.add_argument(
        "--log-format",
        default="console",
        choices=["console", "json"],
        help="console: human readable; json: one JSON object per line "
        "(default: %(default)s)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    setup_logging(level=args.log_level, fmt=args.log_format)
    set_output_dir(args.out_dir)
    try:
        main(args.config_file)
    except KeyboardInterrupt:
        log.warning("Interrupted")
        sys.exit(130)
    except Exception:
        log.exception("Testbench setup failed")
        sys.exit(1)
