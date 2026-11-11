"""
Networks of the testbench.

Every cluster has a network of its own, whatever its runtime: a docker
bridge network (kind, k3d, minikube docker driver) or a libvirt network
(minikube kvm2 driver). The shared infrastructure (e.g. the registry proxy)
has a docker network too. The subnets are /24s of a single pool
(`network.cidr`): the first one to the infrastructure, then one per cluster
in configuration order.

The host is the gateway of every network: it routes between them without
NAT, as allowed by `network.links` (default: every cluster reaches every
other one), through the iptables rules of utils.firewall.
"""

import ipaddress
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable

from core.input import NetworkConfig
from core.log import get_logger
from core.settings import INFRA_NETWORK_NAME, cluster_network_name
from core.types import NodeNetwork
from utils import docker_utils, firewall, libvirt_utils

log = get_logger(__name__)

# Linux distributions whose firewall (firewalld) may drop the traffic routed
# between the networks, whatever the iptables rules (see the README)
FIREWALLD_DISTRIBUTIONS = {"fedora"}

OS_RELEASE_PATH = "/etc/os-release"


@dataclass
class NetworkPlan:
    """Addressing and connectivity of the testbench networks."""

    pool: str
    infra: NodeNetwork
    clusters: dict[str, NodeNetwork]
    # Pairs of clusters that reach each other
    links: set[frozenset[str]]


def plan_networks(config: NetworkConfig, clusters: list[str]) -> NetworkPlan:
    """Subnets of the networks and links between the clusters."""
    pool = str(ipaddress.IPv4Network(config.cidr))
    subnets = (str(s) for s in ipaddress.IPv4Network(pool).subnets(new_prefix=24))
    infra = NodeNetwork(name=INFRA_NETWORK_NAME, cidr=next(subnets), pool=pool)
    networks = {
        name: NodeNetwork(
            name=cluster_network_name(name), cidr=next(subnets), pool=pool
        )
        for name in clusters
    }
    if config.links is None:
        links = {frozenset(pair) for pair in combinations(clusters, 2)}
    else:
        links = {frozenset(pair) for pair in config.links}
    return NetworkPlan(pool=pool, infra=infra, clusters=networks, links=links)


def _os_release() -> dict[str, str]:
    """Fields of /etc/os-release (empty if missing)."""
    fields = {}
    try:
        with open(OS_RELEASE_PATH) as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep:
                    fields[key] = value.strip().strip("\"'")
    except OSError:
        pass
    return fields


def is_firewalld_distribution() -> bool:
    """Whether the host runs Fedora (or a derivative)."""
    release = _os_release()
    ids = {release.get("ID", ""), *release.get("ID_LIKE", "").split()}
    return bool(ids & FIREWALLD_DISTRIBUTIONS)


def check_host(runtimes: Iterable[Any]) -> None:
    """
    Checks the host for the networks of the given runtimes: with docker
    networks, warns on Fedora and requires the iptables firewall backend.
    """
    if not any(runtime.network_type == "docker" for runtime in runtimes):
        return

    if is_firewalld_distribution():
        log.warning(
            "Fedora detected: firewalld may drop the traffic routed between the "
            "testbench networks, whatever the testbench iptables rules. If the "
            "clusters cannot reach each other, see the Compatibility section "
            "of the README"
        )

    backend = docker_utils.firewall_backend()
    if not backend.startswith("iptables"):
        raise RuntimeError(
            f"Docker uses the '{backend}' firewall backend: the testbench "
            'requires the iptables one (remove `"firewall-backend": '
            '"nftables"` from /etc/docker/daemon.json and restart Docker)'
        )


def uses_firewall(plan: NetworkPlan, runtimes: Iterable[Any], infra: bool) -> bool:
    """
    Whether host firewall rules are needed: with more than one network, or
    with libvirt networks (whose DHCP server and traffic are not allowed
    by libvirt in `open` mode).
    """
    runtimes = list(runtimes)
    networks = len(runtimes) + (1 if infra else 0)
    return networks > 1 or any(r.network_type == "libvirt" for r in runtimes)


def ensure_networks(plan: NetworkPlan, runtimes: dict[str, Any], infra: bool) -> None:
    """Creates (or updates) the networks of the clusters and of the infrastructure."""
    for name, runtime in runtimes.items():
        network = plan.clusters[name]
        log.info(
            "Ensuring network",
            cluster=name,
            network=network.name,
            type=runtime.network_type,
            cidr=network.cidr,
        )
        if runtime.network_type == "libvirt":
            libvirt_utils.ensure_libvirt_network(
                network.name, network.cidr, network.gateway, network.pool
            )
        else:
            docker_utils.ensure_docker_network(
                network.name, network.cidr, network.gateway
            )

    if infra:
        log.info("Ensuring network", network=plan.infra.name, cidr=plan.infra.cidr)
        docker_utils.ensure_docker_network(
            plan.infra.name, plan.infra.cidr, plan.infra.gateway
        )


def apply_firewall(plan: NetworkPlan, runtimes: dict[str, Any], infra: bool) -> None:
    """Routes the networks as allowed by the plan."""
    libvirt_bridges = [
        libvirt_utils.bridge_name(plan.clusters[name].name)
        for name, runtime in runtimes.items()
        if runtime.network_type == "libvirt"
    ]
    chains = firewall.build_chains(
        pool=plan.pool,
        clusters={name: plan.clusters[name].cidr for name in runtimes},
        links=plan.links,
        infra=plan.infra.cidr if infra else None,
        libvirt_bridges=libvirt_bridges,
    )
    log.info(
        "Applying firewall rules",
        links=",".join(
            "<->".join(sorted(link)) for link in sorted(map(sorted, plan.links))
        )
        or "-",
    )
    firewall.apply_chains(chains)
