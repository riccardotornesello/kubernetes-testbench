import yaml
import os
import subprocess
from typing import Literal

from kubernetes import config, client
from pydantic import Field

from runtimes.base import (
    BaseRuntime,
    BaseRuntimeSpec,
    CNI_PLUGINS_VERSION,
    NODE_PROXY_CA_PATH,
    NODE_TIER_LABEL,
    node_tier,
)
from utils.docker_utils import (
    client as docker_client,
    ensure_kernel_modules,
    install_cni_plugins,
)
from core.settings import DOCKER_NETWORK_NAME
from utils.kubeconfig import get_kubeconfig_location


class K3dRuntimeSpec(BaseRuntimeSpec):
    # Node image: determines the Kubernetes version
    image: str = "docker.io/rancher/k3s:v1.30.2-k3s2"
    # k3s packaged components not to deploy (k3s --disable). servicelb
    # provides the LoadBalancer services (used e.g. by the Liqo gateway).
    disable: list[
        Literal["traefik", "servicelb", "metrics-server", "local-storage"]
    ] = Field(default_factory=list)


class K3dRuntime(BaseRuntime[K3dRuntimeSpec]):
    @property
    def provides_load_balancer(self) -> bool:
        # k3s ServiceLB (klipper-lb), unless disabled
        return "servicelb" not in self.spec.disable

    def check_dependencies(self):
        subprocess.run(
            ["k3d", "--version"],
            check=True,
        )

    def init_cluster(self) -> None:
        cluster_config = self._gen_config()
        cluster_config_yaml = yaml.dump(cluster_config)

        # Create the cluster using k3d CLI
        subprocess.run(
            [
                "k3d",
                "cluster",
                "create",
                self.name,
                "--config",
                "-",
                "--kubeconfig-update-default=false",
            ],
            input=cluster_config_yaml.encode(),
            check=True,
        )

        # Save kubeconfig content
        kubeconfig_content = self._get_kubeconfig_content()
        kubeconfig_location = get_kubeconfig_location(self.name)

        os.makedirs(os.path.dirname(kubeconfig_location), exist_ok=True)
        with open(kubeconfig_location, "w") as f:
            f.write(kubeconfig_content)

    def cleanup(self) -> None:
        subprocess.run(
            ["k3d", "cluster", "delete", self.name],
            check=True,
        )

    def _get_kubeconfig_content(self) -> str:
        result = subprocess.run(
            ["k3d", "kubeconfig", "get", self.name],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def load_kernel_modules(self, modules: list[str]) -> None:
        # Nodes are containers sharing the host kernel: loading on one is enough
        ensure_kernel_modules(self._get_nodes()[0], modules)

    def install_cni_plugins(self, plugins: list[str]) -> None:
        # k3s ships the CNI plugins in its own data dir, used only by the
        # embedded flannel: copy them in /opt/cni/bin, used by the other CNIs.
        install_cni_plugins(self._get_nodes(), plugins, CNI_PLUGINS_VERSION)

    def _get_nodes(self) -> list[str]:
        containers = docker_client.containers.list(
            filters={"label": f"k3d.cluster={self.name}"}
        )
        return [
            c.name
            for c in containers
            if c.labels.get("k3d.role") in ("server", "agent")
        ]

    def _gen_k3s_server_args(self) -> list[str]:
        args = [
            f"--cluster-cidr={self.settings.cluster_cidr}",
            f"--service-cidr={self.settings.service_cidr}",
            # The CNI is always installed by the testbench
            "--flannel-backend=none",
            "--disable-network-policy",
            *[f"--disable={component}" for component in self.spec.disable],
        ]
        if self.cni.replaces_kube_proxy:
            args.append("--disable-kube-proxy")
        return args

    def _gen_config(self) -> dict:
        conf = {
            "apiVersion": "k3d.io/v1alpha5",
            "kind": "Simple",
            "image": self.spec.image,
            "servers": 1,
            "agents": self.settings.nodes - 1,
            "network": DOCKER_NETWORK_NAME,
            "options": {
                "k3s": {
                    "extraArgs": [
                        {"arg": arg, "nodeFilters": ["server:*"]}
                        for arg in self._gen_k3s_server_args()
                    ],
                    "nodeLabels": [
                        {
                            "label": f"{NODE_TIER_LABEL}={node_tier(i)}",
                            "nodeFilters": ["server:0" if i == 0 else f"agent:{i - 1}"],
                        }
                        for i in range(self.settings.nodes)
                    ],
                }
            },
            "env": [],
            "volumes": [],
        }

        if self.node_proxy is not None:
            # k3s (and its embedded containerd) reads the proxy from its
            # environment, and the CA is mounted: set at creation time.
            address = self.node_proxy_address()
            conf["env"].extend(
                [
                    {
                        "envVar": f"HTTP_PROXY={address}",
                        "nodeFilters": ["all"],
                    },
                    {
                        "envVar": f"HTTPS_PROXY={address}",
                        "nodeFilters": ["all"],
                    },
                    {
                        # Passed as-is to the container: no quotes
                        "envVar": f"NO_PROXY={self.node_no_proxy()}",
                        "nodeFilters": ["all"],
                    },
                ]
            )
            conf["volumes"].append(
                {
                    "volume": f"{self.node_proxy.ca_file}:{NODE_PROXY_CA_PATH}",
                    "nodeFilters": ["all"],
                }
            )

        return conf

    def get_api_server_address(self) -> str:
        kubeconfig_location = get_kubeconfig_location(self.name)
        k8s_client = config.new_client_from_config(config_file=kubeconfig_location)
        v1 = client.CoreV1Api(k8s_client)

        label_selector = "node-role.kubernetes.io/control-plane"

        nodes = v1.list_node(label_selector=label_selector)
        for node in nodes.items:
            for addr in node.status.addresses:
                if addr.type == "InternalIP":
                    return f"https://{addr.address}:6443"

        raise RuntimeError("API server address not found")


module = K3dRuntime
spec = K3dRuntimeSpec
