"""Entry point: creates (up) or deletes (down) the clusters of a configuration file."""

import argparse
import os
import sys

from core.input import validate_config_file, ClusterConfig
from core.log import get_logger, setup_logging
from core.paths import DEFAULT_OUTPUT_DIR, set_output_dir
from core.modules import runtimes, cnis, tools as tool_modules
from core.networking import (
    NetworkPlan,
    apply_firewall,
    check_host,
    ensure_networks,
    plan_networks,
    remove_firewall,
    remove_networks,
    uses_firewall,
)
from core.summary import report_nodes
from core.types import Settings
from core.workloads import create_cluster_workloads
from utils.firewall import ensure_privileges
from utils.kubeconfig import get_kubeconfig_location

log = get_logger("testbench")

COMMANDS = ("up", "down")


def create_settings(config: ClusterConfig, plan: NetworkPlan) -> Settings:
    """Settings of a cluster, shared by its runtime, CNI and the tools."""
    return Settings(
        runtime=config.runtime.type,
        cni=config.cni.type,
        name=config.name,
        nodes=config.nodes,
        cluster_cidr=config.cluster_cidr,
        service_cidr=config.service_cidr,
        network=plan.clusters[config.name],
    )


def create_cni_instance(config: ClusterConfig, plan: NetworkPlan):
    """CNI of the cluster."""
    return cnis[config.cni.type](
        cluster_name=config.name,
        settings=create_settings(config, plan),
        spec=config.cni.spec,
    )


def create_runtime_instance(config: ClusterConfig, plan: NetworkPlan, cni):
    """Runtime of the cluster, which installs `cni`."""
    return runtimes[config.runtime.type](
        name=config.name,
        settings=create_settings(config, plan),
        spec=config.runtime.spec,
        cni=cni,
    )


def create_tool_instance(tool_config):
    """Tool from its configuration."""
    return tool_modules[tool_config.type](spec=tool_config.spec)


class Testbench:
    """Runtimes, CNIs and tools of a configuration, with its network plan."""

    def __init__(self, config_file: str) -> None:
        cfg = validate_config_file(config_file)
        if cfg is None:
            sys.exit(1)
        self.cfg = cfg
        self.plan = plan_networks(cfg.network, [c.name for c in cfg.clusters])
        self.tools = [create_tool_instance(tool_cfg) for tool_cfg in cfg.tools]
        self.clusters = {}
        for cluster_cfg in cfg.clusters:
            cni = create_cni_instance(cluster_cfg, self.plan)
            self.clusters[cluster_cfg.name] = {
                "runtime": create_runtime_instance(cluster_cfg, self.plan, cni),
                "cni": cni,
            }
        self.runtimes = {name: c["runtime"] for name, c in self.clusters.items()}
        self.cnis = {name: c["cni"] for name, c in self.clusters.items()}
        self.settings = {name: r.settings for name, r in self.runtimes.items()}
        self.infra = any(tool.uses_infra_network for tool in self.tools)
        self.firewall = uses_firewall(self.plan, self.runtimes.values(), self.infra)

        log.info(
            "Configuration loaded",
            clusters=",".join(self.clusters),
            tools=",".join(tool_cfg.type for tool_cfg in cfg.tools) or "-",
        )

    def existing_clusters(self) -> list[str]:
        """Names of the configured clusters that already exist."""
        return [name for name, r in self.runtimes.items() if r.exists()]


def confirm(question: str) -> bool:
    """Asks a yes/no question on the terminal (default: no)."""
    sys.stderr.write(f"{question} [y/N] ")
    sys.stderr.flush()
    answer = sys.stdin.readline()
    return answer.strip().lower() in ("y", "yes")


def confirm_deletion(existing: list[str], force: bool) -> None:
    """
    Asks to confirm the deletion of the existing clusters, unless `force`.
    Exits if refused, or if there is no terminal to ask on.
    """
    if not existing or force:
        return
    names = ", ".join(existing)
    if not sys.stdin.isatty():
        log.error(
            "Clusters already exist: pass --force to delete and recreate them",
            clusters=",".join(existing),
        )
        sys.exit(1)
    if not confirm(f"Clusters already exist and will be deleted: {names}. Continue?"):
        log.warning("Aborted: no cluster deleted")
        sys.exit(1)


def up(config_file: str, log_format: str = "console", force: bool = False) -> None:
    """Creates the clusters, deleting the existing ones (confirmed unless `force`)."""
    tb = Testbench(config_file)
    cfg, tools, clusters, plan = tb.cfg, tb.tools, tb.clusters, tb.plan
    all_runtimes, all_cnis, all_settings = tb.runtimes, tb.cnis, tb.settings
    infra, firewall = tb.infra, tb.firewall

    # Host compatibility (firewall backend, distribution), before anything
    check_host(all_runtimes.values())

    log.info("Checking dependencies")
    for cluster in clusters.values():
        cluster["runtime"].check_dependencies()
        cluster["cni"].check_dependencies()
    for tool in tools:
        tool.check_dependencies()

    # Existing clusters are deleted: ask first (before sudo)
    existing = tb.existing_clusters()
    confirm_deletion(existing, force)

    if firewall:
        ensure_privileges()

    for name in existing:
        log.info("Deleting existing cluster", cluster=name)
        all_runtimes[name].cleanup()

    # One network per cluster (and one for the shared infrastructure), created
    # after the old clusters are deleted (minikube may delete its network)
    ensure_networks(plan, all_runtimes, infra)

    # Hook: ONCE in total, before any cluster is created (shared infrastructure)
    for tool in tools:
        tool.before_all_operations(
            all_settings=all_settings,
            all_runtimes=all_runtimes,
            all_cnis=all_cnis,
        )

    # Routing between the networks, before the nodes need it (e.g. to pull
    # through the registry proxy)
    if firewall:
        apply_firewall(plan, all_runtimes, infra)

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
        create_cluster_workloads(kubeconfig_path, cluster_cfg)

    log.info("Testbench ready")
    report_nodes(all_runtimes, log_format)


def down(config_file: str) -> None:
    """Deletes the clusters, the shared infrastructure, networks and firewall rules."""
    tb = Testbench(config_file)

    log.info("Checking dependencies")
    for runtime in tb.runtimes.values():
        runtime.check_dependencies()

    if tb.firewall:
        ensure_privileges()

    for name, runtime in tb.runtimes.items():
        if runtime.exists():
            log.info("Deleting cluster", cluster=name)
            runtime.cleanup()
        else:
            log.info("Cluster not found", cluster=name)
        kubeconfig_path = get_kubeconfig_location(name)
        if os.path.exists(kubeconfig_path):
            os.remove(kubeconfig_path)

    # Hook: ONCE in total, after the clusters are deleted
    for tool in tb.tools:
        tool.teardown()

    remove_networks(tb.plan, tb.runtimes)
    if tb.firewall:
        remove_firewall()

    log.info("Testbench removed")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command line arguments."""
    if argv is None:
        argv = sys.argv[1:]
    # Backward compatible: `main.py <config.yaml>` is `main.py up <config.yaml>`
    if argv and argv[0] not in (*COMMANDS, "-h", "--help"):
        argv = ["up", *argv]

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("config_file", help="YAML configuration file")
    common.add_argument(
        "--out-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="directory of the generated files: kubeconfigs, caches, "
        "registry proxy data (default: %(default)s)",
    )
    common.add_argument(
        "--log-level",
        default="INFO",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="minimum level of the logs (default: %(default)s)",
    )
    common.add_argument(
        "--log-format",
        default="console",
        choices=["console", "json"],
        help="console: human readable; json: one JSON object per line "
        "(default: %(default)s)",
    )

    parser = argparse.ArgumentParser(
        description="Create or delete the clusters of a testbench configuration."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    up_parser = commands.add_parser(
        "up",
        parents=[common],
        help="create the clusters (existing ones are deleted and recreated)",
    )
    up_parser.add_argument(
        "--force",
        action="store_true",
        help="delete the existing clusters without asking for confirmation",
    )
    commands.add_parser(
        "down",
        parents=[common],
        help="delete the clusters, the shared infrastructure (e.g. registry "
        "proxy), the networks and the firewall rules",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    setup_logging(level=args.log_level, fmt=args.log_format)
    set_output_dir(args.out_dir)
    try:
        if args.command == "up":
            up(args.config_file, args.log_format, args.force)
        else:
            down(args.config_file)
    except KeyboardInterrupt:
        log.warning("Interrupted")
        sys.exit(130)
    except Exception:
        log.exception(
            "Testbench setup failed"
            if args.command == "up"
            else "Testbench removal failed"
        )
        sys.exit(1)
