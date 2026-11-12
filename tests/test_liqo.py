import unittest
from unittest import mock

from pydantic import ValidationError

from cnis.calico import CalicoCNI, CalicoCNISpec
from cnis.cilium import CiliumCNI, CiliumCNISpec
from cnis.flannel import FlannelCNI, FlannelCNISpec
from tools.liqo import (
    NOT_ON_LIQO_VIRTUAL_NODES,
    LiqoInstallationConfig,
    LiqoTool,
    LiqoToolSpec,
)
from utils.commands import CommandError
from tests.helpers import make_settings


def liqo(**spec) -> LiqoTool:
    return LiqoTool(LiqoToolSpec(**spec))


def runtime(
    name: str, load_balancer: bool = False, kernel_wireguard: bool = True
) -> mock.Mock:
    """Runtime of the cluster `name` (`name` is a Mock argument: set after)."""
    rt = mock.Mock(
        provides_load_balancer=load_balancer, kernel_wireguard=kernel_wireguard
    )
    rt.name = name
    rt.get_api_server_address.return_value = f"https://{name}:6443"
    return rt


class InstallationConfigTest(unittest.TestCase):
    def test_chart_version(self):
        cases = {
            None: (None, None),
            "latest": (None, None),
            "v1.3.0": (None, "v1.3.0"),
            "https://github.com/liqotech/liqo@abc123": (
                "https://github.com/liqotech/liqo",
                "abc123",
            ),
        }
        for version, expected in cases.items():
            with self.subTest(version=version):
                config = LiqoInstallationConfig(cluster="a", version=version)
                self.assertEqual(config.chart_version(), expected)

    def test_invalid_development_version(self):
        for version in ("@abc", "https://repo@"):
            with self.subTest(version=version):
                with self.assertRaises(ValidationError):
                    LiqoInstallationConfig(cluster="a", version=version)


class SpecValidationTest(unittest.TestCase):
    def test_valid(self):
        LiqoToolSpec(
            installations=[{"cluster": "a"}, {"cluster": "b"}], peerings=[["a", "b"]]
        ).validate_clusters(["a", "b"])

    def test_unknown_cluster(self):
        with self.assertRaisesRegex(ValueError, r"unknown clusters \['c'\]"):
            LiqoToolSpec(installations=[{"cluster": "c"}]).validate_clusters(["a"])

    def test_peering_without_installation(self):
        spec = LiqoToolSpec(installations=[{"cluster": "a"}], peerings=[["a", "b"]])
        with self.assertRaisesRegex(
            ValueError, r"without a Liqo installation: \['b'\]"
        ):
            spec.validate_clusters(["a", "b"])

    def test_unsupported_runtime(self):
        tool = liqo(installations=[{"cluster": "a"}])
        with self.assertRaisesRegex(ValueError, "not supported"):
            tool.before_all_operations({"a": make_settings(runtime="other")}, {}, {})
        tool.before_all_operations({"a": make_settings(runtime="k3d")}, {}, {})


class CniCustomizationTest(unittest.TestCase):
    def pre_cni_install(self, cni, cluster: str = "a"):
        tool = liqo(installations=[{"cluster": "a"}])
        tool.pre_cni_install(cni.settings, runtime(cluster), cni)
        return cni

    def test_not_installed_cluster_is_untouched(self):
        cni = self.pre_cni_install(
            FlannelCNI("b", make_settings(), FlannelCNISpec()), "b"
        )
        self.assertEqual(cni.node_selector_requirements, [])
        self.assertEqual(cni._patches, [])

    def test_agents_kept_off_virtual_nodes(self):
        cni = self.pre_cni_install(FlannelCNI("a", make_settings(), FlannelCNISpec()))
        self.assertEqual(cni.node_selector_requirements, [NOT_ON_LIQO_VIRTUAL_NODES])
        self.assertEqual(cni._patches, [])

    def test_calico_skips_liqo_interfaces(self):
        cni = self.pre_cni_install(CalicoCNI("a", make_settings(), CalicoCNISpec()))
        resources = cni.apply_patches(cni._gen_config())
        self.assertEqual(
            resources[0]["spec"]["calicoNetwork"]["nodeAddressAutodetectionV4"],
            {"skipInterface": "liqo.*"},
        )

    def test_cilium_devices(self):
        cni = self.pre_cni_install(CiliumCNI("a", make_settings(), CiliumCNISpec()))
        self.assertEqual(cni.apply_patches(cni._gen_config())["devices"], ["eth+"])

        # The user devices are kept
        cni = self.pre_cni_install(
            CiliumCNI("a", make_settings(), CiliumCNISpec(devices=["ens3"]))
        )
        self.assertEqual(cni.apply_patches(cni._gen_config())["devices"], ["ens3"])

    def test_cilium_kube_proxy_replacement_warning(self):
        cni = CiliumCNI(
            "a", make_settings(), CiliumCNISpec(kube_proxy_replacement=True)
        )
        with self.assertLogs("tools.liqo", "WARNING"):
            self.pre_cni_install(cni)


@mock.patch("tools.liqo.run_command")
class LiqoctlTest(unittest.TestCase):
    def install(
        self, runtime_type: str, kernel_wireguard: bool = True, **installation
    ) -> None:
        tool = liqo(installations=[{"cluster": "a", **installation}])
        settings = make_settings(name="a", runtime=runtime_type)
        tool.post_cni_install(
            settings, runtime("a", kernel_wireguard=kernel_wireguard), None
        )

    def command(self, run) -> list[str]:
        return run.call_args.args[0]

    def test_kind(self, run):
        self.install("kind", version="v1.3.0")
        command = self.command(run)
        self.assertEqual(command[:4], ["liqoctl", "install", "kind", "--verbose"])
        self.assertEqual(command[command.index("--cluster-id") + 1], "a")
        self.assertEqual(command[command.index("--version") + 1], "v1.3.0")
        self.assertNotIn("--repo-url", command)

    def test_k3d(self, run):
        self.install("k3d")
        command = self.command(run)
        self.assertEqual(command[2], "k3s")
        self.assertNotIn("--version", command)
        self.assertEqual(
            command[-6:],
            [
                "--api-server-url",
                "https://a:6443",
                "--pod-cidr",
                "10.244.0.0/16",
                "--service-cidr",
                "10.96.0.0/16",
            ],
        )

    def test_minikube(self, run):
        self.install("minikube", version="https://repo@sha")
        command = self.command(run)
        self.assertEqual(command[2], "kubeadm")
        self.assertEqual(command[command.index("--repo-url") + 1], "https://repo")
        self.assertEqual(
            command[-2:], ["--set-string", "apiServer.address=https://a:6443"]
        )

    def test_userspace_wireguard_without_kernel_support(self, run):
        self.install("minikube", kernel_wireguard=False)
        self.assertEqual(
            self.command(run)[-2:],
            ["--set", "networking.gatewayTemplates.wireguard.implementation=userspace"],
        )

    def test_kernel_wireguard_by_default(self, run):
        self.install("kind")
        self.assertNotIn("--set", self.command(run))

    def test_not_installed_cluster(self, run):
        liqo(installations=[{"cluster": "b"}]).post_cni_install(
            make_settings(name="a"), runtime("a"), None
        )
        run.assert_not_called()

    @mock.patch("utils.retry.time.sleep")
    def test_install_is_retried(self, sleep, run):
        run.side_effect = [CommandError(1, ["liqoctl"]), None]
        tool = liqo(installations=[{"cluster": "a"}], install_retry={"jitter": 0})
        with self.assertLogs("tools.liqo", "WARNING"):
            tool.post_cni_install(make_settings(name="a"), runtime("a"), None)
        self.assertEqual(run.call_count, 2)

    @mock.patch("tools.liqo.ensure_kubernetes_namespace")
    def test_peering_and_offloading(self, ensure_namespace, run):
        tool = liqo(
            installations=[
                {"cluster": "a", "offloadings": ["ns"]},
                {"cluster": "b"},
                {"cluster": "c"},
            ],
            peerings=[["a", "b"], ["a", "c"]],
        )
        runtimes = {
            "a": runtime("a"),
            "b": runtime("b", load_balancer=True),
            "c": runtime("c"),
        }
        tool.after_all_operations({}, runtimes, {})

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([c[1] for c in commands], ["peer", "peer", "offload"])
        service_types = [
            c[c.index("--gw-server-service-type") + 1] for c in commands[:2]
        ]
        self.assertEqual(service_types, ["LoadBalancer", "NodePort"])
        self.assertEqual(commands[2][:4], ["liqoctl", "offload", "namespace", "ns"])
        self.assertEqual(ensure_namespace.call_args.args[1], "ns")


if __name__ == "__main__":
    unittest.main()
