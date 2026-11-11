"""Discovery of the runtimes, CNIs and tools (one module each)."""

import importlib
import os
import pkgutil
from typing import TypeVar

from runtimes.base import BaseRuntime
from cnis.base import BaseCNI
from tools.base import BaseTool


# Packages are looked up in the repository, whatever the working directory
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RUNTIMES_PATH = "runtimes"
CNIS_PATH = "cnis"
TOOLS_PATH = "tools"


T = TypeVar("T")


def load_modules(path: str, attribute_name: str) -> dict[str, type[T]]:
    """`{module name: module.<attribute_name>}` for the modules of package `path`."""
    modules: dict[str, type[T]] = {}

    for _, module_name, _ in pkgutil.iter_modules([os.path.join(_ROOT, path)]):
        module = importlib.import_module(f"{path}.{module_name}")
        if hasattr(module, attribute_name):
            modules[module_name] = getattr(module, attribute_name)

    return modules


runtimes: dict[str, type[BaseRuntime]] = load_modules(RUNTIMES_PATH, "module")
cnis: dict[str, type[BaseCNI]] = load_modules(CNIS_PATH, "module")
tools: dict[str, type[BaseTool]] = load_modules(TOOLS_PATH, "module")
runtime_specs: dict[str, type] = load_modules(RUNTIMES_PATH, "spec")
tool_specs: dict[str, type] = load_modules(TOOLS_PATH, "spec")
cni_specs: dict[str, type] = load_modules(CNIS_PATH, "spec")
