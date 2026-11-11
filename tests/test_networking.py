import io
import unittest
from unittest import mock

from core.input import NetworkConfig
from core.networking import (
    apply_firewall,
    check_host,
    ensure_networks,
    is_firewalld_distribution,
    plan_networks,
    uses_firewall,
)
from core.summary import format_table
from utils import firewall
from utils.libvirt_utils import bridge_name, network_xml


def runtime(network_type: str = "docker"):
    return mock.Mock(network_type=network_type)


class PlanTest(unittest.TestCase):
    def test_subnets_in_configuration_order(self):
        plan = plan_networks(NetworkConfig(), ["a", "b"])
        self.assertEqual(plan.infra.name, "testbench_infra")
        self.assertEqual(plan.infra.cidr, "10.250.0.0/24")
        self.assertEqual(plan.clusters["a"].name, "testbench-a")
        self.assertEqual(plan.clusters["a"].cidr, "10.250.1.0/24")
        self.assertEqual(plan.clusters["a"].gateway, "10.250.1.1")
        self.assertEqual(plan.clusters["b"].cidr, "10.250.2.0/24")
        self.assertEqual(plan.clusters["b"].pool, "10.250.0.0/16")

    def test_links_default_to_full_mesh(self):
        plan = plan_networks(NetworkConfig(), ["a", "b", "c"])
        self.assertEqual(
            plan.links,
            {frozenset("ab"), frozenset("ac"), frozenset("bc")},
        )

    def test_explicit_links(self):
        plan = plan_networks(NetworkConfig(links=[("b", "a")]), ["a", "b", "c"])
        self.assertEqual(plan.links, {frozenset("ab")})
        self.assertEqual(plan_networks(NetworkConfig(links=[]), ["a"]).links, set())

    def test_uses_firewall(self):
        plan = plan_networks(NetworkConfig(), ["a", "b"])
        self.assertFalse(uses_firewall(plan, [runtime()], infra=False))
        self.assertTrue(uses_firewall(plan, [runtime()], infra=True))
        self.assertTrue(uses_firewall(plan, [runtime(), runtime()], infra=False))
        self.assertTrue(uses_firewall(plan, [runtime("libvirt")], infra=False))


class FirewallRulesTest(unittest.TestCase):
    def chains(self, links, infra="10.250.0.0/24", bridges=()):
        return {
            chain.name: chain
            for chain in firewall.build_chains(
                pool="10.250.0.0/16",
                clusters={
                    "a": "10.250.1.0/24",
                    "b": "10.250.2.0/24",
                    "c": "10.250.3.0/24",
                },
                links=links,
                infra=infra,
                libvirt_bridges=list(bridges),
            )
        }

    @staticmethod
    def accepted(chain) -> list[tuple[str, str]]:
        return [(rule[1], rule[3]) for rule in chain.rules if rule[-1] == "ACCEPT"]

    def test_forward(self):
        chain = self.chains({frozenset("ab")})[firewall.FORWARD_CHAIN]
        self.assertEqual(chain.parents, ["DOCKER-USER", "FORWARD"])
        # Traffic within a single network (bridged)
        self.assertIn("--physdev-is-bridged", chain.rules[0])
        self.assertEqual(self.accepted(chain)[0], ("10.250.0.0/16", "10.250.0.0/16"))
        self.assertEqual(
            self.accepted(chain)[1:],
            [
                # Link a <-> b, both directions
                ("10.250.1.0/24", "10.250.2.0/24"),
                ("10.250.2.0/24", "10.250.1.0/24"),
                # Every cluster <-> infrastructure
                ("10.250.1.0/24", "10.250.0.0/24"),
                ("10.250.0.0/24", "10.250.1.0/24"),
                ("10.250.2.0/24", "10.250.0.0/24"),
                ("10.250.0.0/24", "10.250.2.0/24"),
                ("10.250.3.0/24", "10.250.0.0/24"),
                ("10.250.0.0/24", "10.250.3.0/24"),
            ],
        )
        # Anything else between the networks (e.g. a <-> c) is dropped last
        last = chain.rules[-1]
        self.assertEqual(
            (last[1], last[3], last[-1]), ("10.250.0.0/16", "10.250.0.0/16", "DROP")
        )

    def test_without_infra(self):
        chain = self.chains(set(), infra=None)[firewall.FORWARD_CHAIN]
        self.assertEqual(len(self.accepted(chain)), 1)

    def test_no_nat_between_networks(self):
        chain = self.chains(set())[firewall.NAT_CHAIN]
        self.assertEqual((chain.table, chain.parents), ("nat", ["POSTROUTING"]))
        self.assertEqual(len(chain.rules), 1)
        rule = chain.rules[0]
        self.assertEqual(
            (rule[1], rule[3], rule[-1]), ("10.250.0.0/16", "10.250.0.0/16", "ACCEPT")
        )

    def test_libvirt_dhcp_and_dns(self):
        self.assertEqual(self.chains(set())[firewall.INPUT_CHAIN].rules, [])
        rules = self.chains(set(), bridges=["tb-1"])[firewall.INPUT_CHAIN].rules
        self.assertEqual(
            [(r[1], r[3], r[5]) for r in rules],
            [("tb-1", "udp", "67"), ("tb-1", "udp", "53"), ("tb-1", "tcp", "53")],
        )


class FakeIptables:
    """In-memory iptables, recording the commands."""

    def __init__(self, chains: dict[tuple[str, str], list[str]]):
        self.chains = chains
        self.commands: list[list[str]] = []

    def __call__(self, args, capture=False, **_):
        args = list(args)
        self.commands.append(args)
        if args[0] == "sysctl":
            return mock.Mock(stdout="")
        table, *rest = args[3:]
        if rest[0] == "-n":
            rest = rest[1:]
        op, *rest = rest
        if op == "-L":
            if (table, rest[0]) not in self.chains:
                raise firewall.CommandError(1, args)
        elif op == "-N":
            self.chains[(table, rest[0])] = []
        elif op == "-F":
            self.chains[(table, rest[0])] = []
        elif op == "-A":
            self.chains[(table, rest[0])].append(" ".join(rest[1:]))
        elif op == "-I":
            self.chains[(table, rest[0])].insert(int(rest[1]) - 1, " ".join(rest[2:]))
        elif op == "-D":
            self.chains[(table, rest[0])].remove(" ".join(rest[1:]))
        elif op == "-S":
            lines = [f"-N {rest[0]}"] + [
                f"-A {rest[0]} {rule}" for rule in self.chains[(table, rest[0])]
            ]
            return mock.Mock(stdout="\n".join(lines) + "\n")
        return mock.Mock(stdout="")


class FirewallApplyTest(unittest.TestCase):
    def apply(self, chains, initial):
        fake = FakeIptables(initial)
        with (
            mock.patch("utils.firewall.run_command", side_effect=fake),
            mock.patch("utils.firewall.os.geteuid", return_value=0),
            mock.patch("utils.firewall.open", mock.mock_open(read_data="1\n")),
        ):
            firewall.apply_chains(chains)
        return fake

    def test_rebuilds_chain_and_jumps_first(self):
        chain = firewall.Chain(
            "filter", "TB", ["DOCKER-USER", "FORWARD"], [["-j", "ACCEPT"]]
        )
        fake = self.apply(
            [chain],
            {
                ("filter", "FORWARD"): ["-j DOCKER-USER", "-j DOCKER-FORWARD"],
                ("filter", "DOCKER-USER"): ["-j RETURN", "-j TB"],
                ("filter", "TB"): ["-j DROP"],
            },
        )
        self.assertEqual(fake.chains[("filter", "TB")], ["-j ACCEPT"])
        self.assertEqual(fake.chains[("filter", "DOCKER-USER")], ["-j TB", "-j RETURN"])
        self.assertEqual(
            fake.chains[("filter", "FORWARD")], ["-j DOCKER-USER", "-j DOCKER-FORWARD"]
        )

    def test_idempotent_and_without_docker(self):
        chain = firewall.Chain("filter", "TB", ["DOCKER-USER", "FORWARD"], [])
        initial = {("filter", "FORWARD"): ["-j TB", "-j OTHER"]}
        fake = self.apply([chain], initial)
        self.assertEqual(fake.chains[("filter", "FORWARD")], ["-j TB", "-j OTHER"])
        self.assertFalse(any("-I" in c for c in fake.commands))


class HostCheckTest(unittest.TestCase):
    def check(self, backend="iptables", os_release="ID=ubuntu\n", runtimes=None):
        with (
            mock.patch(
                "core.networking.docker_utils.firewall_backend", return_value=backend
            ),
            mock.patch("core.networking.open", mock.mock_open(read_data=os_release)),
        ):
            check_host(runtimes if runtimes is not None else [runtime()])

    def test_iptables_backend(self):
        with self.assertNoLogs("core.networking"):
            self.check()
            self.check(backend="iptables+firewalld")

    def test_nftables_backend(self):
        with self.assertRaisesRegex(RuntimeError, "'nftables' firewall backend"):
            self.check(backend="nftables")
        # Not checked without docker networks
        self.check(backend="nftables", runtimes=[runtime("libvirt")])

    def test_fedora_warning(self):
        with self.assertLogs("core.networking", "WARNING") as logs:
            self.check(os_release='NAME="Fedora Linux"\nID=fedora\n')
        self.assertIn("firewalld", logs.output[0])
        with mock.patch(
            "core.networking.open",
            mock.mock_open(read_data='ID=rocky\nID_LIKE="rhel centos fedora"\n'),
        ):
            self.assertTrue(is_firewalld_distribution())


class EnsureNetworksTest(unittest.TestCase):
    def test_networks_by_type(self):
        plan = plan_networks(NetworkConfig(), ["a", "b"])
        with (
            mock.patch("core.networking.docker_utils.ensure_docker_network") as docker,
            mock.patch(
                "core.networking.libvirt_utils.ensure_libvirt_network"
            ) as libvirt,
        ):
            ensure_networks(plan, {"a": runtime(), "b": runtime("libvirt")}, infra=True)
        self.assertEqual(
            docker.call_args_list,
            [
                mock.call("testbench-a", "10.250.1.0/24", "10.250.1.1"),
                mock.call("testbench_infra", "10.250.0.0/24", "10.250.0.1"),
            ],
        )
        libvirt.assert_called_once_with(
            "testbench-b", "10.250.2.0/24", "10.250.2.1", "10.250.0.0/16"
        )

    def test_apply_firewall(self):
        plan = plan_networks(NetworkConfig(links=[]), ["a", "b"])
        with mock.patch("core.networking.firewall.apply_chains") as apply:
            apply_firewall(plan, {"a": runtime(), "b": runtime("libvirt")}, infra=False)
        chains = {c.name: c for c in apply.call_args.args[0]}
        self.assertIn(bridge_name("testbench-b"), chains[firewall.INPUT_CHAIN].rules[0])


class LibvirtNetworkTest(unittest.TestCase):
    def test_xml(self):
        xml = network_xml("testbench-a", "10.250.1.0/24", "10.250.1.1", "10.250.0.0/16")
        self.assertIn('<forward mode="open" />', xml)
        self.assertIn('<range start="10.250.1.2" end="10.250.1.254" />', xml)
        self.assertIn("dhcp-option=option:router", xml)
        self.assertIn(
            "dhcp-option=option:classless-static-route,10.250.0.0/16,10.250.1.1", xml
        )
        self.assertLessEqual(len(bridge_name("testbench-" + "x" * 63)), 15)


class SummaryTest(unittest.TestCase):
    def test_table(self):
        rows = [
            {
                "cluster": "a",
                "runtime": "kind",
                "network": "testbench-a",
                "cidr": "10.250.1.0/24",
                "node": "a-control-plane",
                "roles": "control-plane",
                "tier": "worker-0",
                "ip": "10.250.1.2",
            },
            {
                "cluster": "a",
                "runtime": "kind",
                "network": "testbench-a",
                "cidr": "10.250.1.0/24",
                "node": "a-worker",
                "roles": "worker",
                "tier": "worker-1",
                "ip": "10.250.1.3",
            },
            {
                "cluster": "b",
                "runtime": "k3d",
                "network": "testbench-b",
                "cidr": "10.250.2.0/24",
                "node": "k3d-b-server-0",
                "roles": "control-plane,master",
                "tier": "worker-0",
                "ip": "10.250.2.2",
            },
        ]
        out = io.StringIO()
        with mock.patch("core.summary.collect_nodes", return_value=rows):
            from core.summary import report_nodes

            report_nodes({}, "console", out=out)
        text = out.getvalue()
        self.assertEqual(text.strip(), format_table(rows))
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], "a (kind, network testbench-a 10.250.1.0/24)")
        self.assertEqual(lines[1].split(), ["NODE", "ROLES", "TIER", "IP"])
        self.assertEqual(
            lines[2].split(),
            ["a-control-plane", "control-plane", "worker-0", "10.250.1.2"],
        )
        self.assertEqual(lines[5], "b (k3d, network testbench-b 10.250.2.0/24)")

    def test_json(self):
        rows = [{"cluster": "a", "node": "n", "ip": "10.250.1.2"}]
        with (
            mock.patch("core.summary.collect_nodes", return_value=rows),
            self.assertLogs("testbench", "INFO") as logs,
        ):
            from core.summary import report_nodes

            report_nodes({}, "json")
        self.assertEqual(logs.records[0].fields, rows[0])


if __name__ == "__main__":
    unittest.main()
