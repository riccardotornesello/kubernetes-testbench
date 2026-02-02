from dataclasses import dataclass


@dataclass
class Settings:
    runtime: str
    cni: str
    name: str
    nodes: int
    cluster_cidr: str
    service_cidr: str
    cache: bool
