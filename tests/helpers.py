"""Shared helpers of the tests."""

import logging
import os
import tempfile
import unittest

from core.types import NodeNetwork, Settings
from core.input import validate_data

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_settings(**overrides) -> Settings:
    values = {
        "runtime": "kind",
        "cni": "flannel",
        "name": "test",
        "nodes": 2,
        "cluster_cidr": "10.244.0.0/16",
        "service_cidr": "10.96.0.0/16",
        "network": NodeNetwork(
            name="testbench-test", cidr="10.250.1.0/24", pool="10.250.0.0/16"
        ),
    }
    values.update(overrides)
    return Settings(**values)


def cluster(name: str = "a", **fields) -> dict:
    """Raw configuration of a cluster (kind by default)."""
    return {"name": name, "runtime": "kind", **fields}


class ValidationTestCase(unittest.TestCase):
    """Validates raw configurations, capturing the reported errors."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, relative_path: str, content: str) -> str:
        path = os.path.join(self.tmp_dir, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path

    def assert_valid(self, data: dict):
        with self.assertNoLogs("core.input", logging.ERROR):
            cfg = validate_data(data, base_dir=self.tmp_dir)
        self.assertIsNotNone(cfg)
        return cfg

    def validation_errors(self, data: dict) -> list[str]:
        """Error lines reported for an invalid configuration."""
        with self.assertLogs("core.input", logging.ERROR) as logs:
            cfg = validate_data(data, base_dir=self.tmp_dir)
        self.assertIsNone(cfg)
        # The first record is the summary ("Configuration validation failed")
        return [record.getMessage() for record in logs.records[1:]]

    def assert_error(self, errors: list[str], *fragments: str) -> None:
        """Some error line contains every fragment."""
        if not any(all(f in line for f in fragments) for line in errors):
            self.fail(f"no error containing {fragments} in:\n" + "\n".join(errors))
