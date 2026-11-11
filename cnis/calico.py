import ipaddress
from typing import Literal, Optional

from pydantic import Field

from cnis.base import BaseCNI, BaseCNISpec
from utils.kubernetes_utils import (
    apply_manifest,
    fetch_manifest,
    get_api_server_endpoint,
)


class CalicoCNISpec(BaseCNISpec):
    version: str = "3.30.3"
    # Linux dataplane. BPF replaces kube-proxy: the cluster is created without it.
    dataplane: Literal["Iptables", "Nftables", "BPF"] = "Iptables"
    encapsulation: Literal[
        "VXLAN", "VXLANCrossSubnet", "IPIP", "IPIPCrossSubnet", "None"
    ] = "VXLAN"
    # Prefix length of the per-node IP blocks
    block_size: int = Field(default=26, ge=20, le=32)
    nat_outgoing: bool = True
    # MTU of the pod interfaces (default: auto-detected)
    mtu: Optional[int] = Field(default=None, ge=576, le=9000)
    # Install the Calico API server, Goldmane (flow aggregator) and Whisker (UI)
    observability: bool = True

    def validate_cluster(
        self,
        cluster_cidr: ipaddress.IPv4Network,
        service_cidr: ipaddress.IPv4Network,
        nodes: int,
    ) -> None:
        if self.block_size < cluster_cidr.prefixlen:
            raise ValueError(
                f"block_size /{self.block_size} is larger than "
                f"cluster_cidr {cluster_cidr}"
            )


class CalicoCNI(BaseCNI[CalicoCNISpec]):
    WORKLOADS = {"tigera-operator": None, "calico-system": None}

    @property
    def replaces_kube_proxy(self) -> bool:
        return self.spec.dataplane == "BPF"

    def install_cni(self) -> None:
        """
        Installs the Tigera operator, then applies the operator resources
        (Installation, APIServer, Goldmane, Whisker): patches receive this
        list of resources (list[dict]), see cnis.base.find_object.
        """
        base_url = (
            "https://raw.githubusercontent.com/projectcalico/calico/"
            f"v{self.spec.version}/manifests"
        )

        if self.spec.dataplane == "BPF":
            # Without kube-proxy, the operator and calico-node cannot reach the
            # API server through its service IP: give them its real endpoint.
            # Must exist before the operator starts.
            host, port = get_api_server_endpoint(self.kubeconfig)
            apply_manifest(
                self.kubeconfig,
                [
                    {
                        "apiVersion": "v1",
                        "kind": "Namespace",
                        "metadata": {"name": "tigera-operator"},
                    },
                    {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {
                            "name": "kubernetes-services-endpoint",
                            "namespace": "tigera-operator",
                        },
                        "data": {
                            "KUBERNETES_SERVICE_HOST": host,
                            "KUBERNETES_SERVICE_PORT": str(port),
                        },
                    },
                ],
            )

        apply_manifest(
            self.kubeconfig, fetch_manifest(f"{base_url}/operator-crds.yaml")
        )
        apply_manifest(
            self.kubeconfig, fetch_manifest(f"{base_url}/tigera-operator.yaml")
        )
        apply_manifest(
            self.kubeconfig,
            self.apply_patches(self._gen_config()),
            timeout=self.spec.timeout,
        )

    def _gen_config(self) -> list[dict]:
        calico_network = {
            "linuxDataplane": self.spec.dataplane,
            "ipPools": [
                {
                    "name": "default-ipv4-ippool",
                    "blockSize": self.spec.block_size,
                    "cidr": self.settings.cluster_cidr,
                    "encapsulation": self.spec.encapsulation,
                    "natOutgoing": "Enabled" if self.spec.nat_outgoing else "Disabled",
                    "nodeSelector": "all()",
                }
            ],
        }
        if self.spec.mtu is not None:
            calico_network["mtu"] = self.spec.mtu
        if self.settings.runtime == "k3d":
            # Required by k3s: https://docs.tigera.io/calico/latest/getting-started/kubernetes/k3s/multi-node-install
            calico_network["containerIPForwarding"] = "Enabled"

        resources = [
            {
                "apiVersion": "operator.tigera.io/v1",
                "kind": "Installation",
                "metadata": {"name": "default"},
                "spec": {"calicoNetwork": calico_network},
            },
        ]

        affinity = self.node_affinity()
        if affinity is not None:
            # Restrict the Calico node agents to the selected nodes
            node_agent_override = {
                "spec": {"template": {"spec": {"affinity": affinity}}}
            }
            resources[0]["spec"]["calicoNodeDaemonSet"] = node_agent_override
            resources[0]["spec"]["csiNodeDriverDaemonSet"] = node_agent_override

        if self.spec.observability:
            resources += [
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

        return resources


module = CalicoCNI
spec = CalicoCNISpec
