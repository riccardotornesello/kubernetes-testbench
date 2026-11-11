"""
Host firewall rules (iptables) routing the testbench networks.

The host is the gateway of every testbench network, so the traffic between
two networks is forwarded by its kernel: the rules decide which networks
reach each other, and keep the original source address (no NAT). They live
in chains owned by the testbench, flushed and rebuilt at every run, jumped
to from the top of the parent chains:

    filter DOCKER-USER (FORWARD without Docker) -> TESTBENCH-FORWARD
    nat    POSTROUTING                          -> TESTBENCH-POSTROUTING
    filter INPUT                                -> TESTBENCH-INPUT

Docker evaluates DOCKER-USER before its own rules (e.g. the isolation
between bridge networks), so an ACCEPT there wins.
"""

import os
from dataclasses import dataclass

from core.log import get_logger
from utils.commands import CommandError, run_command

log = get_logger(__name__)

FORWARD_CHAIN = "TESTBENCH-FORWARD"
NAT_CHAIN = "TESTBENCH-POSTROUTING"
INPUT_CHAIN = "TESTBENCH-INPUT"

IP_FORWARD_PATH = "/proc/sys/net/ipv4/ip_forward"


@dataclass
class Chain:
    """A testbench chain, its rules and the chains jumping to it."""

    table: str
    name: str
    # Candidate parents: the first existing one jumps to the chain
    parents: list[str]
    rules: list[list[str]]


def _comment(text: str) -> list[str]:
    return ["-m", "comment", "--comment", f"testbench: {text}"]


def _accept(src: str, dst: str, comment: str) -> list[str]:
    return ["-s", src, "-d", dst, *_comment(comment), "-j", "ACCEPT"]


def build_chains(
    pool: str,
    clusters: dict[str, str],
    links: set[frozenset[str]],
    infra: str | None,
    libvirt_bridges: list[str],
) -> list[Chain]:
    """
    Chains of the testbench.

    pool: address pool of all the testbench networks.
    clusters: subnet of the network of each cluster.
    links: pairs of clusters that reach each other.
    infra: subnet of the infrastructure network, reached by every cluster
        (None if not in use).
    libvirt_bridges: bridges of the libvirt networks, whose DHCP and DNS
        server (dnsmasq, on the host) must be reachable from the VMs.
    """
    # Traffic within a single network (bridged, seen here only with
    # br_netfilter, e.g. loaded by kind): always allowed, even by the rules
    # left by a previous run with other networks
    forward: list[list[str]] = [
        ["-s", pool, "-d", pool, "-m", "physdev", "--physdev-is-bridged"]
        + [*_comment("same network"), "-j", "ACCEPT"]
    ]
    for link in sorted(sorted(pair) for pair in links):
        a, b = link
        forward.append(_accept(clusters[a], clusters[b], f"{a} -> {b}"))
        forward.append(_accept(clusters[b], clusters[a], f"{b} -> {a}"))
    if infra is not None:
        for name, cidr in clusters.items():
            forward.append(_accept(cidr, infra, f"{name} -> infrastructure"))
            forward.append(_accept(infra, cidr, f"infrastructure -> {name}"))
    # Any other traffic between the testbench networks is not allowed
    forward.append(["-s", pool, "-d", pool, *_comment("not linked"), "-j", "DROP"])

    # No masquerading between the testbench networks: the original source
    # address is kept (Docker masquerades whatever leaves a bridge network)
    nat = [["-s", pool, "-d", pool, *_comment("no NAT"), "-j", "ACCEPT"]]

    inputs = [
        ["-i", bridge, "-p", proto, "--dport", port, *_comment(service), "-j", "ACCEPT"]
        for bridge in libvirt_bridges
        for proto, port, service in (
            ("udp", "67", "DHCP"),
            ("udp", "53", "DNS"),
            ("tcp", "53", "DNS"),
        )
    ]

    return [
        Chain("filter", FORWARD_CHAIN, ["DOCKER-USER", "FORWARD"], forward),
        Chain("nat", NAT_CHAIN, ["POSTROUTING"], nat),
        Chain("filter", INPUT_CHAIN, ["INPUT"], inputs),
    ]


def _sudo() -> list[str]:
    """Prefix of the commands that need root."""
    return [] if os.geteuid() == 0 else ["sudo"]


def ensure_privileges() -> None:
    """Asks the sudo password now (if needed), not in the middle of the run."""
    if os.geteuid() != 0:
        log.info("Root privileges needed for the firewall rules: running sudo")
        run_command(["sudo", "-v"])


def _iptables(table: str, *args: str) -> str:
    return run_command(
        [*_sudo(), "iptables", "-w", "-t", table, *args], capture=True
    ).stdout


def _chain_exists(table: str, chain: str) -> bool:
    try:
        _iptables(table, "-n", "-L", chain)
        return True
    except CommandError:
        return False


def _ensure_first_jump(table: str, parent: str, chain: str) -> None:
    """Makes the jump to `chain` the first rule of `parent`."""
    jump = f"-A {parent} -j {chain}"
    rules = [
        line.strip()
        for line in _iptables(table, "-S", parent).splitlines()
        if line.startswith("-A ")
    ]
    if rules and rules[0] == jump:
        return
    for _ in range(rules.count(jump)):
        _iptables(table, "-D", parent, "-j", chain)
    _iptables(table, "-I", parent, "1", "-j", chain)


def ensure_ip_forward() -> None:
    """Enables the IPv4 forwarding of the host, if disabled."""
    with open(IP_FORWARD_PATH) as f:
        if f.read().strip() == "1":
            return
    log.warning("IPv4 forwarding disabled on the host: enabling it")
    run_command([*_sudo(), "sysctl", "-w", "net.ipv4.ip_forward=1"], capture=True)


def apply_chains(chains: list[Chain]) -> None:
    """Creates (or rebuilds) the chains and jumps to them."""
    ensure_ip_forward()
    for chain in chains:
        if not _chain_exists(chain.table, chain.name):
            _iptables(chain.table, "-N", chain.name)
        _iptables(chain.table, "-F", chain.name)
        for rule in chain.rules:
            _iptables(chain.table, "-A", chain.name, *rule)
        parent = next((p for p in chain.parents if _chain_exists(chain.table, p)), None)
        if parent is None:
            raise RuntimeError(
                f"None of the chains {chain.parents} exists in the {chain.table} table"
            )
        _ensure_first_jump(chain.table, parent, chain.name)
        log.debug(
            "Firewall chain applied",
            table=chain.table,
            chain=chain.name,
            parent=parent,
            rules=len(chain.rules),
        )
