from abc import ABC, abstractmethod

from core.types import Settings


class BaseCNI(ABC):
    def __init__(self, cluster_name: str, settings: Settings) -> None:
        self.cluster_name = cluster_name
        self.settings = settings

    def check_dependencies(self) -> None:
        return

    @abstractmethod
    def install_cni(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")
