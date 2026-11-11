from abc import ABC, abstractmethod
from typing import TypeVar, Generic

from pydantic import BaseModel

from core.types import Settings
from cnis.base import BaseCNI


SpecT = TypeVar("SpecT", bound=BaseModel)

# Version of the reference CNI plugins (containernetworking/plugins) copied
# into the nodes when the CNI requires them and the node image lacks them.
CNI_PLUGINS_VERSION = "1.9.1"


class BaseRuntime(ABC, Generic[SpecT]):
    """
    A runtime always creates the cluster WITHOUT its built-in CNI (and without
    kube-proxy if the CNI replaces it): the CNI is always installed by the
    testbench, so that every CNI receives the same parameters on every runtime.
    """

    proxy_address: str | None = None

    def __init__(
        self,
        name: str,
        settings: Settings,
        spec: SpecT,
        cni: BaseCNI,
    ):
        self.name = name
        self.settings = settings
        self.spec = spec
        self.cni = cni

    def set_proxy(self, proxy_address: str) -> None:
        self.proxy_address = proxy_address

    def check_dependencies(self) -> None:
        pass

    @abstractmethod
    def init_cluster(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")

    def install_cni_plugins(self, plugins: list[str]) -> None:
        """
        Makes sure the given reference CNI plugins are in /opt/cni/bin on
        every node. Runtimes whose node image already ships them do nothing.
        """
        pass

    def load_kernel_modules(self, modules: list[str]) -> None:
        """
        Makes sure the given kernel modules are loaded on every node.
        VM-based runtimes, whose nodes already load them, do nothing.
        """
        pass

    def install_cni(self) -> None:
        self.load_kernel_modules(self.cni.REQUIRED_KERNEL_MODULES)
        self.install_cni_plugins(self.cni.REQUIRED_CNI_PLUGINS)
        self.cni.install_cni()
        if self.cni.spec.wait:
            self.cni.wait_ready()

    @abstractmethod
    def cleanup(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")

    @abstractmethod
    def get_api_server_address(self) -> str:
        raise NotImplementedError("Subclasses must implement this method.")
