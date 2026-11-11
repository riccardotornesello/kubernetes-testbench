import unittest
from unittest import mock

import core.paths
from core.settings import INFRA_NETWORK_NAME
from tools.registry_proxy import (
    CONFIG_LABEL,
    RegistryProxyTool,
    RegistryProxyToolSpec,
)


class RegistryProxyStartTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_output_dir = core.paths._output_dir
        self.addCleanup(setattr, core.paths, "_output_dir", self._old_output_dir)

    def start(self, output_dir: str, **spec) -> dict:
        """Arguments of the proxy container started with `output_dir`."""
        core.paths.set_output_dir(output_dir)
        container = mock.Mock()
        container.attrs = {
            "NetworkSettings": {
                "Networks": {INFRA_NETWORK_NAME: {"IPAddress": "10.250.0.2"}}
            }
        }
        tool = RegistryProxyTool(RegistryProxyToolSpec(**spec))
        with (
            mock.patch(
                "tools.registry_proxy.ensure_docker_container", return_value=container
            ) as ensure,
            mock.patch(
                "tools.registry_proxy.ensure_container_connected",
                side_effect=lambda c, _: c,
            ) as connect,
            mock.patch.object(RegistryProxyTool, "_wait_ready"),
        ):
            proxy = tool._start()
        connect.assert_called_once_with(container, INFRA_NETWORK_NAME)
        return {"proxy": proxy, **ensure.call_args.kwargs}

    def test_node_proxy(self):
        started = self.start("/out")
        proxy = started["proxy"]
        self.assertEqual(proxy.address, "http://10.250.0.2:3128")
        self.assertEqual(proxy.ca_file, "/out/registry-proxy/ca/ca.crt")
        self.assertEqual(started["network"], INFRA_NETWORK_NAME)
        # Reached through the host routing: never published on the host
        self.assertNotIn("ports", started)

    def test_uses_infra_network(self):
        self.assertTrue(RegistryProxyTool(RegistryProxyToolSpec()).uses_infra_network)
        self.assertFalse(
            RegistryProxyTool(RegistryProxyToolSpec(clusters=[])).uses_infra_network
        )

    def test_volumes_in_output_dir(self):
        volumes = self.start("/out")["volumes"]
        self.assertEqual(volumes["/out/registry-proxy/ca"]["bind"], "/ca")
        self.assertEqual(
            volumes["/out/registry-proxy/cache"]["bind"], "/docker_mirror_cache"
        )

    def test_config_hash(self):
        label = lambda started: started["labels"][CONFIG_LABEL]  # noqa: E731
        base = label(self.start("/out"))
        self.assertEqual(base, label(self.start("/out")))
        # A container of another output directory serves another CA: recreated
        self.assertNotEqual(base, label(self.start("/other")))
        self.assertNotEqual(base, label(self.start("/out", manifest_cache=False)))


if __name__ == "__main__":
    unittest.main()
