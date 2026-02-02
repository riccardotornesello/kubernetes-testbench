import pkgutil
import importlib
from typing import TypeVar

from runtimes.base import BaseRuntime
from cnis.base import BaseCNI
from tools.base import BaseTool


RUNTIMES_PATH = "runtimes"
CNIS_PATH = "cnis"
TOOLS_PATH = "tools"


T = TypeVar("T")


def load_modules(path: str, attribute_name: str) -> dict[str, type[T]]:
    modules: dict[str, type[T]] = {}

    for _, module_name, _ in pkgutil.iter_modules([path]):
        module = importlib.import_module(f"{path}.{module_name}")
        if hasattr(module, attribute_name):
            modules[module_name] = getattr(module, attribute_name)

    return modules


runtimes: dict[str, type[BaseRuntime]] = load_modules(RUNTIMES_PATH, "module")
cnis: dict[str, type[BaseCNI]] = load_modules(CNIS_PATH, "module")
tools: dict[str, type[BaseTool]] = load_modules(TOOLS_PATH, "module")
runtime_specs: dict[str, type] = load_modules(RUNTIMES_PATH, "spec")
tool_specs: dict[str, type] = load_modules(TOOLS_PATH, "spec")
