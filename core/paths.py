"""
Output directory of the testbench: kubeconfigs, caches and the registry proxy
data are written under it. Set once at startup (`--out-dir`, see main.py).
"""

import os

DEFAULT_OUTPUT_DIR = "out"

_output_dir = os.path.abspath(DEFAULT_OUTPUT_DIR)


def set_output_dir(path: str) -> None:
    """Sets the output directory (relative paths are resolved against the cwd)."""
    global _output_dir
    _output_dir = os.path.abspath(path)


def output_path(*parts: str) -> str:
    """Absolute path of `parts` inside the output directory."""
    return os.path.join(_output_dir, *parts)
