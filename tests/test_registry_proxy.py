import unittest
from unittest import mock

import core.paths
from tools.registry_proxy import (
    CONFIG_LABEL,
    RegistryProxyTool,
    RegistryProxyToolSpec,
)


class RegistryProxyStartTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_output_dir = core.paths._output_dir
        self.addCleanup(setattr, core.paths, "_output_dir", self._old_output_dir)

    def start(self, output_dir: str, publish: bool = False, **spec) -> dict:
        """Arguments of the proxy container started with `output_dir`."""
        core.paths.set_output_dir(output_dir)
        container = mock.Mock()
        container.attrs = {
            "NetworkSettings": {"Networks": {"kind": {"IPAddress": "172.18.0.9"}}}
        }
        tool = RegistryProxyTool(RegistryProxyToolSpec(**spec))
        with (
            mock.patch("tools.registry_proxy.ensure_docker_network"),
            mock.patch(
                "tools.registry_proxy.ensure_docker_container", return_value=container
            ) as ensure,
            mock.patch("tools.registry_proxy.get_main_ip", return_value="192.168.1.10"),
            mock.patch.object(RegistryProxyTool, "_wait_ready"),
        ):
            proxy = tool._start(publish)
        return {"proxy": proxy, **ensure.call_args.kwargs}

    def test_node_proxy(self):
        started = self.start("/out", publish=True)
        proxy = started["proxy"]
        self.assertEqual(proxy.address, "http://172.18.0.9:3128")
        self.assertEqual(proxy.host_address, "http://192.168.1.10:3128")
        self.assertEqual(proxy.ca_file, "/out/registry-proxy/ca/ca.crt")
        self.assertEqual(started["ports"], {"3128/tcp": 3128})
        self.assertIsNone(self.start("/out")["proxy"].host_address)

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
        self.assertNotEqual(base, label(self.start("/out", publish=True)))
        self.assertNotEqual(base, label(self.start("/out", manifest_cache=False)))


if __name__ == "__main__":
    unittest.main()
