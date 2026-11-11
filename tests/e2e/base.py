"""
End-to-end test infrastructure: runs the real testbench (`main.py`) on a
configuration, then checks the created clusters through the Kubernetes API.

Opt-in: run only with TESTBENCH_E2E=1 (they create real clusters, need
Docker and the runtime CLIs, and take minutes). Environment variables:

    TESTBENCH_E2E=1              enable the end-to-end tests
    TESTBENCH_E2E_RUNTIMES=kind  runtimes to test, comma separated
                                 (default: those whose CLI is installed)
    TESTBENCH_E2E_KEEP=1         keep the clusters after the tests
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from typing import Any

import yaml
from kubernetes import client, config

from tests.helpers import REPO_ROOT

ENABLED = os.environ.get("TESTBENCH_E2E") == "1"
KEEP_CLUSTERS = os.environ.get("TESTBENCH_E2E_KEEP") == "1"

# Prefix of every cluster created by the tests: never clashes with the
# user's clusters (the testbench deletes existing clusters with the same name)
CLUSTER_PREFIX = "tbe2e"

# Seconds allowed to a whole testbench run
RUN_TIMEOUT = 45 * 60
# Seconds allowed to the checks after a successful run
CHECK_TIMEOUT = 5 * 60

RUNTIME_CLIS = {"kind": "kind", "k3d": "k3d", "minikube": "minikube"}

# Runtime configuration used by the tests (minikube on docker: no KVM needed)
RUNTIME_CONFIGS: dict[str, Any] = {
    "kind": "kind",
    "k3d": "k3d",
    "minikube": {"type": "minikube", "spec": {"driver": "docker"}},
}

# Commands deleting a cluster, for the cleanup
DELETE_COMMANDS = {
    "kind": lambda name: ["kind", "delete", "cluster", "--name", name],
    "k3d": lambda name: ["k3d", "cluster", "delete", name],
    "minikube": lambda name: ["minikube", "delete", "-p", name],
}


def selected_runtimes() -> list[str]:
    requested = os.environ.get("TESTBENCH_E2E_RUNTIMES")
    if requested:
        return [r.strip() for r in requested.split(",") if r.strip()]
    return [r for r, cli in RUNTIME_CLIS.items() if shutil.which(cli)]


def require(test: unittest.TestCase, *clis: str) -> None:
    missing = [cli for cli in clis if shutil.which(cli) is None]
    if missing:
        test.skipTest(f"missing CLIs: {', '.join(missing)}")


def wait_until(
    condition: Callable[[], Any],
    describe: Callable[[], str],
    timeout: int = CHECK_TIMEOUT,
) -> Any:
    """Polls `condition` until it returns a truthy value, or fails."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            result = condition()
            if result:
                return result
        except client.ApiException as e:
            if e.status != 404:
                raise
        if time.monotonic() > deadline:
            raise AssertionError(f"timeout after {timeout}s: {describe()}")
        time.sleep(5)


@unittest.skipUnless(ENABLED, "end-to-end tests disabled (set TESTBENCH_E2E=1)")
class E2ETestCase(unittest.TestCase):
    """
    Each test calls `run_testbench(config)`; the clusters it declares are
    deleted at the end of the test (unless TESTBENCH_E2E_KEEP=1).
    """

    def setUp(self) -> None:
        require(self, "docker")
        self.out_dir = tempfile.mkdtemp(prefix="testbench-e2e-")
        self.clusters: dict[str, str] = {}  # name -> runtime
        # Kept for the diagnosis when the testbench run fails
        self.keep_files = False
        self.addCleanup(self._cleanup)

    # -- running the testbench ------------------------------------------------

    def cluster_name(self, suffix: str) -> str:
        return f"{CLUSTER_PREFIX}-{suffix}"[:40]

    def run_testbench(self, cfg: dict) -> list[dict]:
        """
        Runs main.py on `cfg` and fails if it exits with an error or logs an
        error. Returns the log records.
        """
        for cluster in cfg["clusters"]:
            runtime = cluster.get("runtime", (cfg.get("default") or {}).get("runtime"))
            runtime_type = runtime if isinstance(runtime, str) else runtime["type"]
            self.assertTrue(cluster["name"].startswith(CLUSTER_PREFIX))
            self.clusters[cluster["name"]] = runtime_type

        config_path = os.path.join(self.out_dir, "config.yaml")
        with open(config_path, "w") as f:
            yaml.safe_dump(cfg, f)

        stdout_path = os.path.join(self.out_dir, "stdout.log")
        with open(stdout_path, "w") as stdout:
            process = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    config_path,
                    "--out-dir",
                    self.out_dir,
                    "--log-format",
                    "json",
                    # Command output in the stdout file, for the diagnosis
                    "--log-level",
                    "DEBUG",
                ],
                cwd=REPO_ROOT,
                stdout=stdout,
                stderr=subprocess.PIPE,
                text=True,
                timeout=RUN_TIMEOUT,
            )

        records = []
        for line in process.stderr.splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # Not a log record (e.g. an external command writing on stderr)
                records.append({"level": "raw", "message": line})

        errors = [r for r in records if r.get("level") in ("error", "critical")]
        if process.returncode != 0 or errors:
            self.keep_files = True
            tail = "\n".join(
                json.dumps(r) if r.get("level") != "raw" else r["message"]
                for r in records[-40:]
            )
            self.fail(
                f"testbench failed (exit {process.returncode}, "
                f"{len(errors)} error logs). Files in {self.out_dir}. "
                f"Last logs:\n{tail}"
            )
        self.assertTrue(
            any(r.get("message") == "Testbench ready" for r in records),
            "'Testbench ready' not logged",
        )
        return records

    def _cleanup(self) -> None:
        if KEEP_CLUSTERS:
            print(f"\nKeeping clusters {list(self.clusters)}, files in {self.out_dir}")
            return
        for name, runtime in self.clusters.items():
            subprocess.run(
                DELETE_COMMANDS[runtime](name),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if self.keep_files:
            print(f"\nTestbench files kept in {self.out_dir}")
        else:
            shutil.rmtree(self.out_dir, ignore_errors=True)

    # -- Kubernetes checks ----------------------------------------------------

    def api(self, cluster: str) -> client.ApiClient:
        kubeconfig = os.path.join(self.out_dir, "kubeconfigs", f"{cluster}.yaml")
        self.assertTrue(
            os.path.isfile(kubeconfig), f"kubeconfig not written: {kubeconfig}"
        )
        return config.new_client_from_config(config_file=kubeconfig)

    def assert_nodes_ready(self, cluster: str, nodes: int) -> None:
        """`nodes` nodes, all Ready, the i-th labeled tier=worker-<i>."""
        core = client.CoreV1Api(self.api(cluster))
        items = [
            n
            for n in core.list_node().items
            # Liqo virtual nodes are not created by the runtime
            if (n.metadata.labels or {}).get("liqo.io/type") != "virtual-node"
        ]
        self.assertEqual(len(items), nodes, f"{cluster}: nodes")
        for node in items:
            ready = any(
                c.type == "Ready" and c.status == "True"
                for c in node.status.conditions or []
            )
            self.assertTrue(ready, f"{cluster}: node {node.metadata.name} not Ready")
        tiers = sorted((n.metadata.labels or {}).get("tier", "") for n in items)
        self.assertEqual(
            tiers, [f"worker-{i}" for i in range(nodes)], f"{cluster}: tier labels"
        )

    def assert_all_pods_healthy(self, cluster: str) -> None:
        """Every pod of the cluster is Ready (or Succeeded), eventually."""
        core = client.CoreV1Api(self.api(cluster))

        def unhealthy() -> list[str]:
            result = []
            for pod in core.list_pod_for_all_namespaces().items:
                owners = pod.metadata.owner_references or []
                # Job pods may fail and be retried: the Job result is checked
                # by wait_job_succeeded
                if pod.status.phase == "Succeeded" or any(
                    o.kind == "Job" for o in owners
                ):
                    continue
                ready = any(
                    c.type == "Ready" and c.status == "True"
                    for c in pod.status.conditions or []
                )
                if not ready:
                    name = f"{pod.metadata.namespace}/{pod.metadata.name}"
                    result.append(f"{name} ({pod.status.phase})")
            return result

        wait_until(
            lambda: not unhealthy(), lambda: f"{cluster}: pods not ready: {unhealthy()}"
        )

    def assert_cluster_healthy(self, cluster: str, nodes: int) -> None:
        self.assert_nodes_ready(cluster, nodes)
        self.assert_all_pods_healthy(cluster)

    def wait_pod_running(self, cluster: str, namespace: str, name: str) -> client.V1Pod:
        core = client.CoreV1Api(self.api(cluster))

        def running():
            pod = core.read_namespaced_pod(name, namespace)
            return pod if pod.status.phase == "Running" else None

        return wait_until(
            running, lambda: f"{cluster}: pod {namespace}/{name} not running"
        )

    def wait_job_succeeded(self, cluster: str, namespace: str, name: str) -> None:
        batch = client.BatchV1Api(self.api(cluster))

        def status():
            return batch.read_namespaced_job(name, namespace).status

        wait_until(
            lambda: (status().succeeded or 0) >= 1,
            lambda: f"{cluster}: job {namespace}/{name} not succeeded: {status()}",
        )
