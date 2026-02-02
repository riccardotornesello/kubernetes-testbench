from abc import ABC
from typing import Generic, TypeVar, Type, Dict

from pydantic import BaseModel

from core.types import Settings
from runtimes.base import BaseRuntime
from cnis.base import BaseCNI

SpecT = TypeVar("SpecT", bound=BaseModel)


class BaseTool(ABC, Generic[SpecT]):
    def __init__(self, spec: SpecT) -> None:
        self.spec = spec

    def pre_cluster_init(self) -> None:
        pass

    def post_cluster_init(self) -> None:
        pass

    def pre_cni_install(
        self,
        settings: Settings,
        runtime: Type[BaseRuntime],
        cni: Type[BaseCNI],
    ) -> None:
        pass

    def post_cni_install(
        self,
        settings: Settings,
        runtime: Type[BaseRuntime],
        cni: Type[BaseCNI],
    ) -> None:
        pass

    def after_all_operations(
        self,
        all_settings: Dict[str, Settings],
        all_runtimes: Dict[str, Type[BaseRuntime]],
        all_cnis: Dict[str, Type[BaseCNI]],
    ) -> None:
        pass
