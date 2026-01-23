from abc import ABC, abstractmethod

from config import CNIEnum


class Cluster(ABC):
    name: str
    nodes: int
    cluster_cidr: str
    service_cidr: str
    cni: CNIEnum

    def __init__(
        self,
        name: str,
        nodes: int,
        cluster_cidr: str,
        service_cidr: str,
        cni: CNIEnum,
    ):
        self.name = name
        self.nodes = nodes
        self.cluster_cidr = cluster_cidr
        self.service_cidr = service_cidr
        self.cni = cni

    def create(self) -> None:
        self.init_cluster()
        self.install_cni()

    @abstractmethod
    def cleanup(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")

    def get_kubeconfig_location(self) -> str:
        return f"out/kubeconfigs/{self.name}.yaml"

    @abstractmethod
    def init_cluster(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")

    @abstractmethod
    def install_cni(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")
