import os
import tempfile
import unittest
from unittest import mock

import yaml

import core.paths
from cnis.calico import CalicoCNI, CalicoCNISpec
from cnis.flannel import FlannelCNI, FlannelCNISpec
from runtimes.base import NODE_PROXY_CA_PATH, NodeProxy
from runtimes.k3d import K3dRuntime, K3dRuntimeSpec
from runtimes.kind import KindCluster, KindClusterSpec
from runtimes.minikube import MinikubeCluster, MinikubeClusterSpec
from tests.helpers import make_settings

PROXY = NodeProxy(
    address="http://proxy:3128",
    ca_file="/out/ca.crt",
    no_proxy=["localhost"],
)


def make_runtime(cls, spec, nodes: int = 3, bpf: bool = False, **settings):
    settings = make_settings(nodes=nodes, **settings)
    cni = (
        CalicoCNI("test", settings, CalicoCNISpec(dataplane="BPF"))
        if bpf
        else FlannelCNI("test", settings, FlannelCNISpec())
    )
    return cls(name="test", settings=settings, spec=spec, cni=cni)


class NodeProxyTest(unittest.TestCase):
    def test_network_type(self):
        self.assertEqual(
            make_runtime(KindCluster, KindClusterSpec()).network_type, "docker"
        )
        self.assertEqual(
            make_runtime(K3dRuntime, K3dRuntimeSpec()).network_type, "docker"
        )
        self.assertEqual(
            make_runtime(
                MinikubeCluster, MinikubeClusterSpec(driver="kvm2")
            ).network_type,
            "libvirt",
        )
        self.assertEqual(
            make_runtime(
                MinikubeCluster, MinikubeClusterSpec(driver="docker")
            ).network_type,
            "docker",
        )

    def test_kernel_wireguard(self):
        self.assertTrue(make_runtime(KindCluster, KindClusterSpec()).kernel_wireguard)
        # The minikube ISO kernel has no WireGuard; docker nodes use the host's
        self.assertFalse(
            make_runtime(
                MinikubeCluster, MinikubeClusterSpec(driver="kvm2")
            ).kernel_wireguard
        )
        self.assertTrue(
            make_runtime(
                MinikubeCluster, MinikubeClusterSpec(driver="docker")
            ).kernel_wireguard
        )

    def test_no_proxy_includes_cluster_cidrs(self):
        runtime = make_runtime(KindCluster, KindClusterSpec())
        runtime.set_node_proxy(PROXY)
        self.assertEqual(
            runtime.node_no_proxy(), "localhost,10.244.0.0/16,10.96.0.0/16"
        )

    def test_setup_script(self):
        runtime = make_runtime(KindCluster, KindClusterSpec())
        runtime.set_node_proxy(PROXY)
        script = runtime.node_proxy_setup_script("containerd")
        self.assertIn(
            f"curl -fsSL http://proxy:3128/ca.crt -o {NODE_PROXY_CA_PATH}", script
        )
        self.assertIn("/etc/systemd/system/containerd.service.d", script)
        self.assertIn('Environment="HTTPS_PROXY=http://proxy:3128"', script)
        self.assertTrue(script.rstrip().endswith("systemctl restart containerd"))


class KindTest(unittest.TestCase):
    def test_network(self):
        runtime = make_runtime(KindCluster, KindClusterSpec())
        with (
            mock.patch("runtimes.kind.run_command") as run,
            mock.patch("runtimes.kind.get_kubeconfig_location", return_value="/k"),
        ):
            runtime.init_cluster()
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["KIND_EXPERIMENTAL_DOCKER_NETWORK"], "testbench-test")

    def test_config(self):
        config = make_runtime(
            KindCluster, KindClusterSpec(image="kindest/node:v1.31.0")
        )._gen_config()
        self.assertEqual(
            config["networking"],
            {
                "podSubnet": "10.244.0.0/16",
                "serviceSubnet": "10.96.0.0/16",
                "disableDefaultCNI": True,
                "kubeProxyMode": "iptables",
            },
        )
        self.assertEqual(
            config["nodes"],
            [
                {
                    "role": "control-plane",
                    "image": "kindest/node:v1.31.0",
                    "labels": {"tier": "worker-0"},
                },
                {
                    "role": "worker",
                    "image": "kindest/node:v1.31.0",
                    "labels": {"tier": "worker-1"},
                },
                {
                    "role": "worker",
                    "image": "kindest/node:v1.31.0",
                    "labels": {"tier": "worker-2"},
                },
            ],
        )

    def test_no_kube_proxy_when_cni_replaces_it(self):
        config = make_runtime(KindCluster, KindClusterSpec(), bpf=True)._gen_config()
        self.assertEqual(config["networking"]["kubeProxyMode"], "none")

    def test_init_cluster_passes_config_on_stdin(self):
        runtime = make_runtime(KindCluster, KindClusterSpec())
        with mock.patch("runtimes.kind.run_command") as run:
            runtime.init_cluster()
        args, kwargs = run.call_args
        self.assertEqual(args[0][:3], ["kind", "create", "cluster"])
        self.assertIn("disableDefaultCNI: true", kwargs["input"])


class K3dTest(unittest.TestCase):
    def test_config(self):
        runtime = make_runtime(K3dRuntime, K3dRuntimeSpec(disable=["traefik"]))
        config = runtime._gen_config()
        self.assertEqual((config["servers"], config["agents"]), (1, 2))
        self.assertEqual(config["network"], "testbench-test")
        args = [a["arg"] for a in config["options"]["k3s"]["extraArgs"]]
        self.assertEqual(
            args,
            [
                "--cluster-cidr=10.244.0.0/16",
                "--service-cidr=10.96.0.0/16",
                "--flannel-backend=none",
                "--disable-network-policy",
                "--disable=traefik",
            ],
        )
        self.assertEqual(
            [
                (n["label"], n["nodeFilters"])
                for n in config["options"]["k3s"]["nodeLabels"]
            ],
            [
                ("tier=worker-0", ["server:0"]),
                ("tier=worker-1", ["agent:0"]),
                ("tier=worker-2", ["agent:1"]),
            ],
        )
        self.assertEqual((config["env"], config["volumes"]), ([], []))

    def test_disable_kube_proxy(self):
        runtime = make_runtime(K3dRuntime, K3dRuntimeSpec(), bpf=True)
        self.assertIn("--disable-kube-proxy", runtime._gen_k3s_server_args())

    def test_node_proxy_set_at_creation(self):
        runtime = make_runtime(K3dRuntime, K3dRuntimeSpec())
        runtime.set_node_proxy(PROXY)
        config = runtime._gen_config()
        self.assertEqual(
            [e["envVar"] for e in config["env"]],
            [
                "HTTP_PROXY=http://proxy:3128",
                "HTTPS_PROXY=http://proxy:3128",
                "NO_PROXY=localhost,10.244.0.0/16,10.96.0.0/16",
            ],
        )
        self.assertEqual(
            config["volumes"][0]["volume"], f"/out/ca.crt:{NODE_PROXY_CA_PATH}"
        )

    def test_load_balancer(self):
        self.assertTrue(
            make_runtime(K3dRuntime, K3dRuntimeSpec()).provides_load_balancer
        )
        self.assertFalse(
            make_runtime(
                K3dRuntime, K3dRuntimeSpec(disable=["servicelb"])
            ).provides_load_balancer
        )


class MinikubeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_output_dir = core.paths._output_dir
        core.paths.set_output_dir(self._tmp.name)

    def tearDown(self) -> None:
        core.paths._output_dir = self._old_output_dir
        self._tmp.cleanup()

    def run_init(self, runtime: MinikubeCluster) -> list[list[str]]:
        """Commands run by init_cluster."""
        with (
            mock.patch("runtimes.minikube.run_command") as run,
            mock.patch("runtimes.minikube.label_nodes"),
            mock.patch.object(
                MinikubeCluster, "_get_nodes", return_value=["test", "test-m02"]
            ),
        ):
            runtime.init_cluster()
        for _, kwargs in run.call_args_list:
            self.assertEqual(
                kwargs["env"]["KUBECONFIG"],
                os.path.join(self._tmp.name, "kubeconfigs", "test.yaml"),
            )
        return [args[0] for args, _ in run.call_args_list]

    def start_args(self, runtime: MinikubeCluster) -> list[str]:
        return self.run_init(runtime)[0]

    def flag(self, args: list[str], name: str) -> list[str]:
        return [args[i + 1] for i, a in enumerate(args) if a == name]

    def test_start_args(self):
        args = self.start_args(
            make_runtime(MinikubeCluster, MinikubeClusterSpec(cpus=4, memory="4g"))
        )
        self.assertEqual(args[:4], ["minikube", "-p", "test", "start"])
        self.assertEqual(self.flag(args, "--nodes"), ["3"])
        self.assertEqual(self.flag(args, "--kubernetes-version"), ["v1.30.0"])
        self.assertEqual(self.flag(args, "--network"), ["testbench-test"])
        # Not "false": refused by minikube with containerd and cri-o
        (cni,) = self.flag(args, "--cni")
        self.assertEqual(
            cni, os.path.join(self._tmp.name, "minikube", "test-cni-placeholder.yaml")
        )
        with open(cni) as f:
            self.assertEqual(yaml.safe_load(f)["kind"], "ConfigMap")
        self.assertEqual(
            self.flag(args, "--service-cluster-ip-range"), ["10.96.0.0/16"]
        )
        self.assertEqual(
            self.flag(args, "--extra-config"),
            ["kubeadm.pod-network-cidr=10.244.0.0/16"],
        )
        self.assertEqual(self.flag(args, "--cpus"), ["4"])
        self.assertEqual(self.flag(args, "--memory"), ["4g"])
        self.assertEqual(self.flag(args, "--disk-size"), [])

    def test_kvm2_routes_pool_through_host(self):
        commands = self.run_init(make_runtime(MinikubeCluster, MinikubeClusterSpec()))
        route = "sudo ip route replace 10.250.0.0/16 via 10.250.1.1"
        self.assertEqual(
            commands[1:],
            [
                # Profile before `--`: after it, part of the remote command
                ["minikube", "-p", "test", "ssh", "-n", node, "--", route]
                for node in ("test", "test-m02")
            ],
        )

    def test_docker_driver_uses_cluster_network(self):
        runtime = make_runtime(MinikubeCluster, MinikubeClusterSpec(driver="docker"))
        commands = self.run_init(runtime)
        self.assertEqual(self.flag(commands[0], "--network"), ["testbench-test"])
        # No route: the default route of the container nodes is the host
        self.assertEqual(len(commands), 1)

    def test_skip_kube_proxy(self):
        args = self.start_args(
            make_runtime(MinikubeCluster, MinikubeClusterSpec(), bpf=True)
        )
        self.assertIn(
            "kubeadm.skip-phases=addon/kube-proxy", self.flag(args, "--extra-config")
        )


if __name__ == "__main__":
    unittest.main()
