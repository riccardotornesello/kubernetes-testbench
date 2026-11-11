import os
import subprocess
import sys
import unittest

from tests.helpers import REPO_ROOT


class ModuleDiscoveryTest(unittest.TestCase):
    def test_components(self):
        from core import modules

        self.assertEqual(sorted(modules.runtimes), ["k3d", "kind", "minikube"])
        self.assertEqual(sorted(modules.cnis), ["calico", "cilium", "flannel"])
        self.assertEqual(sorted(modules.tools), ["liqo", "registry_proxy"])
        self.assertEqual(modules.runtimes.keys(), modules.runtime_specs.keys())

    def test_independent_of_working_directory(self):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from core import modules; print(sorted(modules.runtimes))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, REPO_ROOT],
            cwd=os.path.dirname(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "['k3d', 'kind', 'minikube']")


if __name__ == "__main__":
    unittest.main()
