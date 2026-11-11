import copy
import ipaddress
import json
import unittest
from unittest import mock

from pydantic import ValidationError

from cnis.base import find_object
from cnis.calico import CalicoCNI, CalicoCNISpec
from cnis.cilium import CiliumCNI, CiliumCNISpec
from cnis.flannel import FlannelCNI, FlannelCNISpec
from tests.helpers import make_settings

REQUIREMENT = {"key": "liqo.io/type", "operator": "DoesNotExist"}

# Relevant subset of the kube-flannel.yml manifest
FLANNEL_MANIFEST = [
    {"kind": "Namespace", "metadata": {"name": "kube-flannel"}},
    {
        "kind": "ConfigMap",
        "metadata": {"name": "kube-flannel-cfg"},
        "data": {"net-conf.json": "{}"},
    },
    {
        "kind": "DaemonSet",
        "metadata": {"name": "kube-flannel-ds"},
        "spec": {
            "template": {
                "spec": {
                    "affinity": {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [
                                    {
                                        "matchExpressions": [
                                            {
                                                "key": "kubernetes.io/os",
                                                "operator": "In",
                                                "values": ["linux"],
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                    "containers": [
                        {"name": "kube-flannel", "args": ["--ip-masq"]},
                    ],
                }
            }
        },
    },
]


def flannel(**spec) -> FlannelCNI:
    return FlannelCNI("test", make_settings(), FlannelCNISpec(**spec))


def calico(runtime: str = "kind", **spec) -> CalicoCNI:
    return CalicoCNI(
        "test", make_settings(cni="calico", runtime=runtime), CalicoCNISpec(**spec)
    )


def cilium(**spec) -> CiliumCNI:
    return CiliumCNI("test", make_settings(cni="cilium"), CiliumCNISpec(**spec))


class BaseCNITest(unittest.TestCase):
    def test_node_affinity(self):
        cni = flannel()
        self.assertIsNone(cni.node_affinity())
        cni.add_node_selector_requirement(REQUIREMENT)
        self.assertEqual(
            cni.node_affinity(),
            {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [{"matchExpressions": [REQUIREMENT]}]
                    }
                }
            },
        )

    def test_patches_in_order(self):
        cni = flannel()
        cni.add_patch(lambda config: config.append("in-place"))
        cni.add_patch(lambda config: [*config, "replaced"])
        self.assertEqual(cni.apply_patches([]), ["in-place", "replaced"])

    def test_find_object(self):
        self.assertIs(
            find_object(FLANNEL_MANIFEST, "ConfigMap", "kube-flannel-cfg"),
            FLANNEL_MANIFEST[1],
        )
        self.assertIsNone(find_object(FLANNEL_MANIFEST, "ConfigMap", "nope"))

    def test_spec_rejects_unknown_fields(self):
        with self.assertRaises(ValidationError):
            FlannelCNISpec(foo=1)


class FlannelTest(unittest.TestCase):
    def customize(self, cni: FlannelCNI) -> list[dict]:
        return [cni._customize(obj) for obj in copy.deepcopy(FLANNEL_MANIFEST)]

    def test_net_conf(self):
        objects = self.customize(flannel(backend="host-gw", enable_nftables=True))
        conf = json.loads(
            find_object(objects, "ConfigMap", "kube-flannel-cfg")["data"][
                "net-conf.json"
            ]
        )
        self.assertEqual(
            conf,
            {
                "Network": "10.244.0.0/16",
                "EnableNFTables": True,
                "Backend": {"Type": "host-gw"},
            },
        )

    def daemonset_spec(self, objects: list[dict]) -> dict:
        return find_object(objects, "DaemonSet", "kube-flannel-ds")["spec"]["template"][
            "spec"
        ]

    def test_defaults_leave_daemonset_unchanged(self):
        objects = self.customize(flannel())
        self.assertEqual(
            self.daemonset_spec(objects), self.daemonset_spec(FLANNEL_MANIFEST)
        )

    def test_node_selector_requirements_extend_affinity(self):
        cni = flannel()
        cni.add_node_selector_requirement(REQUIREMENT)
        spec = self.daemonset_spec(self.customize(cni))
        terms = spec["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"]
        self.assertEqual(terms[0]["matchExpressions"][-1], REQUIREMENT)
        self.assertEqual(len(terms[0]["matchExpressions"]), 2)

    def test_iface(self):
        spec = self.daemonset_spec(self.customize(flannel(iface="eth1")))
        self.assertEqual(spec["containers"][0]["args"], ["--ip-masq", "--iface=eth1"])

    def test_install_applies_customized_and_patched_objects(self):
        cni = flannel()
        cni.add_patch(lambda objects: objects[:1])
        with (
            mock.patch(
                "cnis.flannel.fetch_manifest",
                return_value=copy.deepcopy(FLANNEL_MANIFEST),
            ) as fetch,
            mock.patch("cnis.flannel.apply_manifest") as apply,
        ):
            cni.install_cni()
        self.assertIn("v0.28.10/kube-flannel.yml", fetch.call_args.args[0])
        self.assertEqual(apply.call_args.args[1], [FLANNEL_MANIFEST[0]])

    def test_does_not_replace_kube_proxy(self):
        self.assertFalse(flannel().replaces_kube_proxy)


class CalicoTest(unittest.TestCase):
    def test_installation(self):
        resources = calico(
            encapsulation="IPIP", block_size=24, nat_outgoing=False, mtu=1400
        )._gen_config()
        network = find_object(resources, "Installation", "default")["spec"][
            "calicoNetwork"
        ]
        self.assertEqual(network["linuxDataplane"], "Iptables")
        self.assertEqual(network["mtu"], 1400)
        self.assertEqual(
            network["ipPools"][0],
            {
                "name": "default-ipv4-ippool",
                "blockSize": 24,
                "cidr": "10.244.0.0/16",
                "encapsulation": "IPIP",
                "natOutgoing": "Disabled",
                "nodeSelector": "all()",
            },
        )
        self.assertNotIn("containerIPForwarding", network)

    def test_k3d_enables_container_ip_forwarding(self):
        resources = calico(runtime="k3d")._gen_config()
        network = resources[0]["spec"]["calicoNetwork"]
        self.assertEqual(network["containerIPForwarding"], "Enabled")

    def test_observability(self):
        kinds = [r["kind"] for r in calico()._gen_config()]
        self.assertEqual(kinds, ["Installation", "APIServer", "Goldmane", "Whisker"])
        kinds = [r["kind"] for r in calico(observability=False)._gen_config()]
        self.assertEqual(kinds, ["Installation"])

    def test_node_affinity(self):
        cni = calico()
        cni.add_node_selector_requirement(REQUIREMENT)
        spec = cni._gen_config()[0]["spec"]
        for daemonset in ("calicoNodeDaemonSet", "csiNodeDriverDaemonSet"):
            self.assertEqual(
                spec[daemonset]["spec"]["template"]["spec"]["affinity"],
                cni.node_affinity(),
            )

    def test_bpf_replaces_kube_proxy(self):
        self.assertFalse(calico().replaces_kube_proxy)
        self.assertTrue(calico(dataplane="BPF").replaces_kube_proxy)

    def test_validate_cluster(self):
        spec = CalicoCNISpec(block_size=20)
        spec.validate_cluster(ipaddress.IPv4Network("10.0.0.0/16"), None, 1)
        with self.assertRaisesRegex(ValueError, "block_size"):
            spec.validate_cluster(ipaddress.IPv4Network("10.0.0.0/24"), None, 1)


class CiliumTest(unittest.TestCase):
    def test_values(self):
        values = cilium(
            tunnel_protocol="geneve",
            cluster_pool_mask_size=26,
            mtu=1400,
            devices=["eth0"],
        )._gen_config()
        self.assertEqual(
            values,
            {
                "routingMode": "tunnel",
                "tunnelProtocol": "geneve",
                "ipam": {
                    "mode": "cluster-pool",
                    "operator": {
                        "clusterPoolIPv4PodCIDRList": ["10.244.0.0/16"],
                        "clusterPoolIPv4MaskSize": 26,
                    },
                },
                "kubeProxyReplacement": False,
                "MTU": 1400,
                "devices": ["eth0"],
            },
        )

    def test_kube_proxy_replacement_sets_api_server_endpoint(self):
        cni = cilium(kube_proxy_replacement=True)
        self.assertTrue(cni.replaces_kube_proxy)
        with mock.patch(
            "cnis.cilium.get_api_server_endpoint", return_value=("172.18.0.2", 6443)
        ):
            values = cni._gen_config()
        self.assertEqual(
            (values["k8sServiceHost"], values["k8sServicePort"]), ("172.18.0.2", 6443)
        )

    def test_node_affinity(self):
        cni = cilium()
        cni.add_node_selector_requirement(REQUIREMENT)
        self.assertEqual(cni._gen_config()["affinity"], cni.node_affinity())

    def test_validate_cluster(self):
        spec = CiliumCNISpec(cluster_pool_mask_size=24)
        spec.validate_cluster(ipaddress.IPv4Network("10.0.0.0/23"), None, 2)
        with self.assertRaisesRegex(ValueError, "not contain enough"):
            spec.validate_cluster(ipaddress.IPv4Network("10.0.0.0/23"), None, 3)
        with self.assertRaisesRegex(ValueError, "larger than"):
            CiliumCNISpec(cluster_pool_mask_size=16).validate_cluster(
                ipaddress.IPv4Network("10.0.0.0/24"), None, 1
            )


if __name__ == "__main__":
    unittest.main()
