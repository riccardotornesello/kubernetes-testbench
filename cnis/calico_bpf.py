import tempfile
import requests
from kubernetes import utils, config, client

from cnis.base import BaseCNI
from utils.kubeconfig import get_kubeconfig_location


class CalicoCNI(BaseCNI):
    VERSION = "3.30.3"  # TODO: move to spec

    def install_cni(self) -> None:
        kubeconfig_location = get_kubeconfig_location(self.cluster_name)
        k8s_client = config.new_client_from_config(config_file=kubeconfig_location)

        # Save manifests to temp files and apply them
        for url in [
            f"https://raw.githubusercontent.com/projectcalico/calico/v{self.VERSION}/manifests/operator-crds.yaml",
            f"https://raw.githubusercontent.com/projectcalico/calico/v{self.VERSION}/manifests/tigera-operator.yaml",
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
                        "linuxDataplane": "BPF",
                        "bpfNetworkBootstrap": "Enabled",
                        "kubeProxyReplacement": "Enabled",
                        "nodeAddressAutodetectionV4": {"skipInterface": "liqo.*"},
                        "ipPools": [
                            {
                                "name": "default-ipv4-ippool",
                                "blockSize": 26,
                                "cidr": self.settings.cluster_cidr,
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


module = CalicoCNI
