"""Data types shared by runtimes, CNIs and tools."""

from dataclasses import dataclass


@dataclass
class Settings:
    """Settings of a cluster, shared by its runtime, its CNI and the tools."""

    runtime: str
    cni: str
    name: str
    nodes: int
    cluster_cidr: str
    service_cidr: str
