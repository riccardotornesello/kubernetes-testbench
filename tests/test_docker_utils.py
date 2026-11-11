import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from unittest import mock

from utils import docker_utils


def make_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in ("bridge", "loopback"):
            data = f"#!{name}".encode()
            info = tarfile.TarInfo(f"./{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class InstallCniPluginsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.archive = make_archive()

    def responses(self, checksum: str):
        def get(url, timeout):
            response = mock.Mock()
            response.content = self.archive
            response.text = f"{checksum}  archive.tgz\n"
            return response

        return get

    def install(self, checksum: str) -> mock.Mock:
        container = mock.Mock()
        container.exec_run.return_value = mock.Mock(exit_code=0, output=b"")
        container.put_archive.return_value = True
        client = mock.Mock()
        client.containers.get.return_value = container
        with (
            mock.patch(
                "utils.docker_utils.requests.get", side_effect=self.responses(checksum)
            ),
            mock.patch("utils.docker_utils.get_client", return_value=client),
        ):
            docker_utils.install_cni_plugins(
                ["node"], ["bridge"], "1.0.0", cache_dir=self._tmp.name
            )
        return container

    def test_verified_archive_is_cached_and_copied(self):
        container = self.install(hashlib.sha256(self.archive).hexdigest())
        self.assertEqual(len(os.listdir(self._tmp.name)), 1)
        path, payload = container.put_archive.call_args.args
        self.assertEqual(path, "/opt/cni/bin")
        with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
            self.assertEqual(tar.getnames(), ["bridge"])

    def test_checksum_mismatch(self):
        with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
            self.install("0" * 64)
        # Nothing cached: the next run downloads it again
        self.assertEqual(os.listdir(self._tmp.name), [])


if __name__ == "__main__":
    unittest.main()
