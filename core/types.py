"""Data types shared by runtimes, CNIs and tools."""

import ipaddress
from dataclasses import dataclass


@dataclass(frozen=True)
class NodeNetwork:
    """
    Network the nodes of a cluster are attached to: a docker or libvirt
    network of its own, with a /24 of the testbench address pool. The host is
    the gateway of every network and routes between them (see core.networking).
    """

    # Docker or libvirt network name
    name: str
    # Subnet of the network (IPv4 CIDR)
    cidr: str
    # Address pool of all the testbench networks: routed through the gateway
    pool: str

    @property
    def gateway(self) -> str:
        """Address of the host on the network (first address of the subnet)."""
        return str(next(ipaddress.IPv4Network(self.cidr).hosts()))


@dataclass
class Settings:
    """Settings of a cluster, shared by its runtime, its CNI and the tools."""

    runtime: str
    cni: str
    name: str
    nodes: int
    cluster_cidr: str
    service_cidr: str
    network: NodeNetwork
