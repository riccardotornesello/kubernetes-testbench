from abc import ABC, abstractmethod


class CNI(ABC):
    kubeconfig: str
    cidr: str

    def __init__(self, kubeconfig: str, cidr: str) -> None:
        self.kubeconfig = kubeconfig
        self.cidr = cidr

    @abstractmethod
    def install(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")
