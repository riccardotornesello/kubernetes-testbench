import ipaddress
import os
import subprocess
import tempfile
from typing import Literal, Optional

import yaml
from pydantic import Field

from cnis.base import BaseCNI, BaseCNISpec
from utils.kubernetes_utils import get_api_server_endpoint


class CiliumCNISpec(BaseCNISpec):
    version: str = "1.18.6"
    # Replace kube-proxy with Cilium: the cluster is created without it
    kube_proxy_replacement: bool = False
    tunnel_protocol: Literal["vxlan", "geneve"] = "vxlan"
    # Prefix length of the per-node pod CIDRs (cluster-pool IPAM)
    cluster_pool_mask_size: int = Field(default=24, ge=16, le=30)
    # MTU of the pod interfaces (default: auto-detected)
    mtu: Optional[int] = Field(default=None, ge=576, le=9000)
    # Interfaces facing the cluster/external network, used by the eBPF
    # datapath (kube-proxy replacement, BPF masquerading, host firewall) and
    # by the MTU auto-detection. '+' is a wildcard (e.g. "eth+").
    # Default: auto-detected by Cilium.
    devices: Optional[list[str]] = Field(default=None, min_length=1)

    def validate_cluster(
        self,
        cluster_cidr: ipaddress.IPv4Network,
        service_cidr: ipaddress.IPv4Network,
        nodes: int,
    ) -> None:
        if self.cluster_pool_mask_size < cluster_cidr.prefixlen:
            raise ValueError(
                f"cluster_pool_mask_size /{self.cluster_pool_mask_size} is larger "
                f"than cluster_cidr {cluster_cidr}"
            )
        if 2 ** (self.cluster_pool_mask_size - cluster_cidr.prefixlen) < nodes:
            raise ValueError(
                f"cluster_cidr {cluster_cidr} does not contain enough "
                f"/{self.cluster_pool_mask_size} subnets for {nodes} nodes"
            )


class CiliumCNI(BaseCNI[CiliumCNISpec]):
    WORKLOADS = {"kube-system": ["cilium", "cilium-operator"]}

    @property
    def replaces_kube_proxy(self) -> bool:
        return self.spec.kube_proxy_replacement

    def check_dependencies(self) -> None:
        try:
            subprocess.run(
                ["cilium", "version", "--client"], capture_output=True, check=True
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "Cilium CLI is not installed or not found in PATH."
            ) from e

    def install_cni(self) -> None:
        """
        Installs Cilium through the cilium CLI: patches receive the Helm
        values (dict).
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as tmp:
            yaml.dump(self.apply_patches(self._gen_config()), tmp)
            tmp.flush()

            subprocess.run(
                [
                    "cilium",
                    "install",
                    "--version",
                    self.spec.version,
                    "--values",
                    tmp.name,
                ],
                env={**os.environ, "KUBECONFIG": os.path.abspath(self.kubeconfig)},
                check=True,
            )

    def _gen_config(self) -> dict:
        values = {
            "routingMode": "tunnel",
            "tunnelProtocol": self.spec.tunnel_protocol,
            "ipam": {
                "mode": "cluster-pool",
                "operator": {
                    "clusterPoolIPv4PodCIDRList": [self.settings.cluster_cidr],
                    "clusterPoolIPv4MaskSize": self.spec.cluster_pool_mask_size,
                },
            },
            "kubeProxyReplacement": self.spec.kube_proxy_replacement,
        }

        if self.spec.kube_proxy_replacement:
            # Without kube-proxy, Cilium cannot reach the API server through
            # its service IP: give it its real endpoint.
            host, port = get_api_server_endpoint(self.kubeconfig)
            values["k8sServiceHost"] = host
            values["k8sServicePort"] = port

        if self.spec.mtu is not None:
            values["MTU"] = self.spec.mtu

        if self.spec.devices is not None:
            values["devices"] = list(self.spec.devices)

        affinity = self.node_affinity()
        if affinity is not None:
            # Restrict the Cilium agent to the selected nodes
            values["affinity"] = affinity

        return values


module = CiliumCNI
spec = CiliumCNISpec
