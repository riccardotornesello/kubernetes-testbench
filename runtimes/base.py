from abc import ABC, abstractmethod
from typing import TypeVar, Generic

from pydantic import BaseModel

from core.types import Settings
from cnis.base import BaseCNI


SpecT = TypeVar("SpecT", bound=BaseModel)


class BaseRuntime(ABC, Generic[SpecT]):
    SUPPORTED_CNIS: list[str] = []
    DEFAULT_CNI: str | None = None

    proxy_address: str | None = None

    def __init__(
        self,
        name: str,
        settings: Settings,
        spec: SpecT,
    ):
        self.name = name
        self.settings = settings
        self.spec = spec

    def set_proxy(self, proxy_address: str) -> None:
        self.proxy_address = proxy_address

    def check_dependencies(self) -> None:
        pass

    @abstractmethod
    def init_cluster(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")

    def install_cni(self, cni: BaseCNI) -> None:
        if self.settings.cni not in self.SUPPORTED_CNIS:
            raise ValueError(
                f"CNI '{self.settings.cni}' is not supported by runtime '{type(self).__name__}'"
            )

        if self.settings.cni != self.DEFAULT_CNI:
            cni.install_cni()

    @abstractmethod
    def cleanup(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")

    @abstractmethod
    def get_api_server_address(self) -> str:
        raise NotImplementedError("Subclasses must implement this method.")
