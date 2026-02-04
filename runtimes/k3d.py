import yaml
import os
import subprocess

from kubernetes import config, client
from pydantic import BaseModel

from runtimes.base import BaseRuntime
from core.settings import DOCKER_NETWORK_NAME
from core.cache import REGISTRY_PROXY_CA_VOLUME
from utils.kubeconfig import get_kubeconfig_location


class K3dRuntimeSpec(BaseModel):
    image: str = "docker.io/rancher/k3s:v1.30.2-k3s2"


class K3dRuntime(BaseRuntime[K3dRuntimeSpec]):
    SUPPORTED_CNIS = ["flannel", "cilium", "calico"]
    DEFAULT_CNI = "flannel"

    def check_dependencies(self):
        subprocess.run(
            ["k3d", "--version"],
            check=True,
        )

    def init_cluster(self) -> None:
        cluster_config = self._gen_config()
        cluster_config_yaml = yaml.dump(cluster_config)

        additional_args = []

        # Disable flannel if another CNI is selected
        # TODO: move to config generation
        if self.settings.cni != self.DEFAULT_CNI:
            additional_args.extend(
                [
                    "--k3s-arg",
                    "--flannel-backend=none@server:*",
                    "--k3s-arg",
                    "--disable-network-policy@server:*",
                ]
            )

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
            ]
            + additional_args,
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
                        {
                            "arg": f"--cluster-cidr={self.settings.cluster_cidr}",
                            "nodeFilters": ["server:*"],
                        },
                        {
                            "arg": f"--service-cidr={self.settings.service_cidr}",
                            "nodeFilters": ["server:*"],
                        },
                    ],
                    "nodeLabels": [
                        {
                            "label": "tier=worker-0",
                            "nodeFilters": ["server:0"],
                        },
                        *[
                            {
                                "label": f"tier=worker-{i}",
                                "nodeFilters": [f"agent:{i - 1}"],
                            }
                            for i in range(1, self.settings.nodes)
                        ],
                    ],
                }
            },
            "env": [],
            "volumes": [],
        }

        if self.proxy_address is not None:
            conf["env"].extend(
                [
                    {
                        "envVar": f"HTTP_PROXY={self.proxy_address}",
                        "nodeFilters": ["all"],
                    },
                    {
                        "envVar": f"HTTPS_PROXY={self.proxy_address}",
                        "nodeFilters": ["all"],
                    },
                    {
                        "envVar": "NO_PROXY='ttl.sh,storage.googleapis.com,localhost,127.0.0.1,0.0.0.0,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,.local,.svc'",
                        "nodeFilters": ["all"],
                    },
                ]
            )
            conf["volumes"].append(
                {
                    "volume": f"{REGISTRY_PROXY_CA_VOLUME}/ca.crt:/etc/ssl/certs/registry-proxy-ca.pem",
                    "nodeFilters": ["all"],
                }
            )

        return conf

    def get_api_server_address(self) -> str:
        kubeconfig_location = get_kubeconfig_location(self.name)
        k8s_client = config.new_client_from_config(config_file=kubeconfig_location)
        v1 = client.CoreV1Api(k8s_client)

        label_selector = "node-role.kubernetes.io/master"

        nodes = v1.list_node(label_selector=label_selector)
        for node in nodes.items:
            for addr in node.status.addresses:
                if addr.type == "InternalIP":
                    return addr.address

        raise RuntimeError("API server address not found")


module = K3dRuntime
spec = K3dRuntimeSpec
