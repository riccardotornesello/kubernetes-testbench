"""
End-to-end tests: real clusters created by the testbench, for every selected
runtime (see tests/e2e/base.py). Run with:

    TESTBENCH_E2E=1 uv run python -m unittest tests.e2e -v
"""

import ipaddress
import os
import unittest

import docker
import yaml
from kubernetes import client

from tests.e2e.base import (
    KEEP_CLUSTERS,
    RUNTIME_CONFIGS,
    E2ETestCase,
    require,
    selected_runtimes,
)
from tools.registry_proxy import CONTAINER_NAME as REGISTRY_PROXY_CONTAINER

# Pod-to-service connectivity through the cluster DNS: proves that the CNI
# and kube-proxy (or its replacement) work
CONNECTIVITY_JOB = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {"name": "connectivity"},
    "spec": {
        "backoffLimit": 20,
        "template": {
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "wget",
                        "image": "busybox:1.37",
                        "command": ["wget", "-q", "-T", "5", "-O-", "http://nginx"],
                    }
                ],
            }
        },
    },
}


def remove_registry_proxy() -> None:
    if KEEP_CLUSTERS:
        return
    try:
        container = docker.from_env().containers.get(REGISTRY_PROXY_CONTAINER)
    except docker.errors.NotFound:
        return
    # The CA and cache files are owned by the container user: deleted from
    # inside, so that the test output directory can be removed
    if container.status == "running":
        container.exec_run(["sh", "-c", "rm -rf /ca/* /docker_mirror_cache/*"])
    container.remove(force=True)


# minikube VMs (examples/liqo_minikube.yaml)
KVM2_SPEC = {"driver": "kvm2", "cpus": 2, "memory": "4g"}

NGINX_POD_SPEC = {"containers": [{"name": "nginx", "image": "nginx:1.27"}]}


class RuntimeE2E(E2ETestCase):
    """Scenarios run on `runtime` (set by the generated subclasses)."""

    runtime = "kind"

    def name(self, suffix: str) -> str:
        return self.cluster_name(f"{self.runtime}-{suffix}")

    def single_cluster(self, suffix: str, **fields) -> dict:
        return {
            "clusters": [
                {
                    "name": self.name(suffix),
                    "runtime": RUNTIME_CONFIGS[self.runtime],
                    **fields,
                }
            ]
        }

    # -- CNIs -----------------------------------------------------------------

    def check_cni(self, cni) -> None:
        cfg = self.single_cluster(
            cni if isinstance(cni, str) else cni["type"], cni=cni, nodes=2
        )
        self.run_testbench(cfg)
        self.assert_cluster_healthy(cfg["clusters"][0]["name"], nodes=2)

    def test_flannel(self):
        self.check_cni("flannel")

    def test_calico(self):
        self.check_cni("calico")

    def test_cilium(self):
        require(self, "cilium")
        self.check_cni("cilium")

    # -- minikube -------------------------------------------------------------

    def assert_pod_ips_in(self, cluster: str, cidr: str) -> None:
        """Every pod not on the host network has an address in `cidr`."""
        core = client.CoreV1Api(self.api(cluster))
        outside = [
            f"{p.metadata.namespace}/{p.metadata.name} ({p.status.pod_ip})"
            for p in core.list_pod_for_all_namespaces().items
            if not p.spec.host_network
            and p.status.pod_ip
            and ipaddress.ip_address(p.status.pod_ip) not in ipaddress.ip_network(cidr)
        ]
        self.assertEqual(outside, [], f"{cluster}: pods outside {cidr}")

    def check_minikube(self, suffix: str, spec: dict, cni: str = "flannel") -> None:
        if self.runtime != "minikube":
            self.skipTest("minikube only")
        cidr = "10.210.0.0/16"
        cfg = self.single_cluster(suffix, nodes=2, cni=cni, cluster_cidr=cidr)
        cfg["clusters"][0]["runtime"] = {"type": "minikube", "spec": spec}
        self.run_testbench(cfg)
        cluster = cfg["clusters"][0]["name"]
        self.assert_cluster_healthy(cluster, nodes=2)
        # No pod got an address from the minikube default bridge CNI
        # (10.244.0.0/16) before the testbench installed its CNI
        self.assert_pod_ips_in(cluster, cidr)

    # One test per container runtime (each test deletes its cluster and
    # network at the end). minikube refuses `--cni false` with containerd and
    # cri-o.

    def test_minikube_containerd(self):
        self.check_minikube(
            "containerd", {"driver": "docker", "container_runtime": "containerd"}
        )

    def test_minikube_crio(self):
        self.check_minikube("crio", {"driver": "docker", "container_runtime": "cri-o"})

    def test_minikube_docker_runtime(self):
        self.check_minikube(
            "dockerrt", {"driver": "docker", "container_runtime": "docker"}
        )

    def test_minikube_kvm2(self):
        # Same runtime as examples/liqo_minikube.yaml: VMs, containerd, Cilium
        require(self, "virsh", "cilium")
        if not os.path.exists("/dev/kvm"):
            self.skipTest("KVM not available")
        self.check_minikube("kvm2", KVM2_SPEC, cni="cilium")

    def test_minikube_kvm2_liqo(self):
        # examples/liqo_minikube.yaml: the minikube ISO kernel has no
        # WireGuard, the Liqo gateways must use the userspace implementation
        require(self, "virsh", "cilium")
        if self.runtime != "minikube":
            self.skipTest("minikube only")
        if not os.path.exists("/dev/kvm"):
            self.skipTest("KVM not available")
        self.check_liqo(
            {"type": "minikube", "spec": KVM2_SPEC}, suffix="-vm", cni="cilium"
        )

    # -- workloads ------------------------------------------------------------

    def test_workloads(self):
        manifest = os.path.join(self.out_dir, "manifests", "app.yaml")
        os.makedirs(os.path.dirname(manifest))
        with open(manifest, "w") as f:
            yaml.safe_dump_all(
                [
                    {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": "from-manifest"},
                        "data": {"key": "value"},
                    },
                    CONNECTIVITY_JOB,
                ],
                f,
            )

        cfg = self.single_cluster(
            "workloads",
            nodes=2,
            namespaces=[
                {
                    "name": "web",
                    # Relative to the configuration file (in out_dir)
                    "manifests": ["manifests/app.yaml"],
                    "deployments": [
                        {"name": "nginx", "replicas": 2, "pod_spec": NGINX_POD_SPEC}
                    ],
                    "services": [
                        {
                            "name": "nginx",
                            "spec": {
                                "selector": {"app": "nginx"},
                                "ports": [{"port": 80}],
                            },
                        }
                    ],
                    "pods": [
                        {
                            "name": "pinned",
                            "spec": {
                                "containers": [
                                    {
                                        "name": "c",
                                        "image": "busybox:1.37",
                                        "command": ["sleep", "infinity"],
                                    }
                                ],
                                "nodeSelector": {"tier": "worker-1"},
                            },
                        }
                    ],
                }
            ],
        )
        self.run_testbench(cfg)
        cluster = cfg["clusters"][0]["name"]
        api = self.api(cluster)

        self.wait_job_succeeded(cluster, "web", "connectivity")
        self.assert_cluster_healthy(cluster, nodes=2)
        apps = client.AppsV1Api(api)
        self.assertEqual(
            apps.read_namespaced_deployment("nginx", "web").status.available_replicas, 2
        )

        core = client.CoreV1Api(api)
        self.assertEqual(
            core.read_namespaced_config_map("from-manifest", "web").data,
            {"key": "value"},
        )
        self.assertEqual(
            core.read_namespaced_service("nginx", "web").spec.ports[0].port, 80
        )

        pod = self.wait_pod_running(cluster, "web", "pinned")
        node = core.read_node(pod.spec.node_name)
        self.assertEqual(node.metadata.labels["tier"], "worker-1")

    # -- tools ----------------------------------------------------------------

    def test_registry_proxy(self):
        cfg = self.single_cluster(
            "proxy",
            namespaces=[
                {"name": "web", "pods": [{"name": "nginx", "spec": NGINX_POD_SPEC}]}
            ],
        )
        cfg["tools"] = ["registry_proxy"]
        # The proxy container has a fixed name: removed after the test (a
        # later testbench run recreates it, its cache is in its own out dir)
        self.addCleanup(remove_registry_proxy)
        # Created by the user, not by Docker (as root): its subdirectories,
        # emptied by remove_registry_proxy, can then be removed
        os.makedirs(os.path.join(self.out_dir, "registry-proxy"))
        self.run_testbench(cfg)
        cluster = cfg["clusters"][0]["name"]

        self.assert_cluster_healthy(cluster, nodes=1)
        self.wait_pod_running(cluster, "web", "nginx")

        proxy = docker.from_env().containers.get(REGISTRY_PROXY_CONTAINER)
        self.assertEqual(proxy.status, "running")
        # The image was pulled through the proxy: it is in its cache (read
        # from the container, the cache files are owned by its user)
        exit_code, output = proxy.exec_run(
            ["sh", "-c", "find /docker_mirror_cache -type f | head -n 1"]
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(output.strip(), "empty registry proxy cache")

    def test_liqo(self):
        self.check_liqo(RUNTIME_CONFIGS[self.runtime])

    def check_liqo(self, runtime_cfg, suffix: str = "", cni=None) -> None:
        """Two clusters peered with Liqo, a pod offloaded to the provider."""
        require(self, "liqoctl")
        consumer = self.name(f"consumer{suffix}")
        provider = self.name(f"provider{suffix}")
        default = {"runtime": runtime_cfg}
        if cni is not None:
            default["cni"] = cni
        cfg = {
            "default": default,
            "clusters": [
                {
                    "name": consumer,
                    "cluster_cidr": "10.200.0.0/16",
                    "service_cidr": "10.71.0.0/16",
                    "namespaces": [
                        {
                            "name": "shared",
                            "pods": [
                                {
                                    "name": "remote",
                                    "spec": {
                                        **NGINX_POD_SPEC,
                                        "nodeSelector": {
                                            "liqo.io/remote-cluster-id": provider
                                        },
                                    },
                                }
                            ],
                        }
                    ],
                },
                {
                    "name": provider,
                    "cluster_cidr": "10.201.0.0/16",
                    "service_cidr": "10.72.0.0/16",
                },
            ],
            "tools": [
                {
                    "type": "liqo",
                    "spec": {
                        "installations": [
                            {"cluster": consumer, "offloadings": ["shared"]},
                            {"cluster": provider},
                        ],
                        "peerings": [[consumer, provider]],
                    },
                }
            ],
        }
        self.run_testbench(cfg)

        self.assert_cluster_healthy(provider, nodes=1)
        self.assert_cluster_healthy(consumer, nodes=1)

        # The consumer got a virtual node for the provider, Ready
        core = client.CoreV1Api(self.api(consumer))
        virtual_nodes = core.list_node(label_selector="liqo.io/type=virtual-node").items
        self.assertEqual(len(virtual_nodes), 1)

        # The offloaded pod runs on it
        pod = self.wait_pod_running(consumer, "shared", "remote")
        self.assertEqual(pod.spec.node_name, virtual_nodes[0].metadata.name)


# One test class per selected runtime (KindE2E, K3dE2E, MinikubeE2E), also
# addressable by name: `python -m unittest tests.e2e.test_clusters.KindE2E`
RUNTIME_CLASSES = [
    type(f"{runtime.capitalize()}E2E", (RuntimeE2E,), {"runtime": runtime})
    for runtime in selected_runtimes()
]
globals().update({cls.__name__: cls for cls in RUNTIME_CLASSES})


def load_tests(loader: unittest.TestLoader, tests, pattern) -> unittest.TestSuite:
    # Only the per-runtime classes, not the RuntimeE2E base
    suite = unittest.TestSuite()
    for cls in RUNTIME_CLASSES:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite
