import tempfile
import requests
from kubernetes import utils, config, client

from cni.base import CNI


class Calico(CNI):
    version: str

    def __init__(self, version: str = "3.30.3", **kwargs) -> None:
        super().__init__(**kwargs)
        self.version = version

    def install(self) -> None:
        k8s_client = config.new_client_from_config(config_file=self.kubeconfig)

        # Save manifests to temp files and apply them
        for url in [
            f"https://raw.githubusercontent.com/projectcalico/calico/v{self.version}/manifests/operator-crds.yaml",
            f"https://raw.githubusercontent.com/projectcalico/calico/v{self.version}/manifests/tigera-operator.yaml",
        ]:
            with tempfile.NamedTemporaryFile() as temp_crds:
                response = requests.get(url)
                response.raise_for_status()

                temp_crds.write(response.content)
                temp_crds.flush()

                utils.create_from_yaml(k8s_client, temp_crds.name)

        # Apply Calico installation configuration
        custom_objects_api = client.CustomObjectsApi(k8s_client)
        for resource in self._gen_config():
            custom_objects_api.create_cluster_custom_object(
                group=resource["apiVersion"].split("/")[0],
                version=resource["apiVersion"].split("/")[1],
                plural=resource["kind"].lower() + "s",
                body=resource,
            )

    def _gen_config(self) -> list[dict]:
        return [
            {
                "apiVersion": "operator.tigera.io/v1",
                "kind": "Installation",
                "metadata": {"name": "default"},
                "spec": {
                    "calicoNetwork": {
                        "nodeAddressAutodetectionV4": {"skipInterface": "liqo.*"},
                        "ipPools": [
                            {
                                "name": "default-ipv4-ippool",
                                "blockSize": 26,
                                "cidr": self.cidr,
                                "encapsulation": "VXLAN",
                                "natOutgoing": "Enabled",
                                "nodeSelector": "all()",
                            }
                        ],
                    }
                },
            },
            {
                "apiVersion": "operator.tigera.io/v1",
                "kind": "APIServer",
                "metadata": {"name": "default"},
                "spec": {},
            },
            {
                "apiVersion": "operator.tigera.io/v1",
                "kind": "Goldmane",
                "metadata": {"name": "default"},
            },
            {
                "apiVersion": "operator.tigera.io/v1",
                "kind": "Whisker",
                "metadata": {"name": "default"},
            },
        ]
