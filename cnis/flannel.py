import json
from typing import Literal, Optional

from cnis.base import BaseCNI, BaseCNISpec, NOT_ON_LIQO_VIRTUAL_NODES
from utils.kubernetes_utils import apply_manifest, fetch_manifest


class FlannelCNISpec(BaseCNISpec):
    version: str = "0.28.10"
    # https://github.com/flannel-io/flannel/blob/master/Documentation/backends.md
    backend: Literal["vxlan", "host-gw", "wireguard"] = "vxlan"
    # Interface for the inter-node traffic (default: the default-route one)
    iface: Optional[str] = None
    # Use nftables instead of iptables for the masquerading rules
    enable_nftables: bool = False


class FlannelCNI(BaseCNI[FlannelCNISpec]):
    # The flannel CNI plugin delegates to bridge/host-local, and the default
    # conflist chains portmap: none of them is installed by kube-flannel.
    REQUIRED_CNI_PLUGINS = ["bridge", "host-local", "loopback", "portmap"]
    # flanneld refuses to start if br_netfilter is not loaded
    REQUIRED_KERNEL_MODULES = ["br_netfilter"]
    WORKLOADS = {"kube-flannel": None}

    def install_cni(self) -> None:
        objects = fetch_manifest(
            "https://github.com/flannel-io/flannel/releases/download/"
            f"v{self.spec.version}/kube-flannel.yml"
        )
        apply_manifest(self.kubeconfig, [self._customize(obj) for obj in objects])

    def _customize(self, obj: dict) -> dict:
        kind = obj["kind"]
        name = obj["metadata"]["name"]

        if kind == "ConfigMap" and name == "kube-flannel-cfg":
            obj["data"]["net-conf.json"] = json.dumps(self._gen_net_conf(), indent=2)

        elif kind == "DaemonSet" and name == "kube-flannel-ds":
            pod_spec = obj["spec"]["template"]["spec"]

            # Keep flannel off Liqo virtual nodes
            terms = pod_spec["affinity"]["nodeAffinity"][
                "requiredDuringSchedulingIgnoredDuringExecution"
            ]["nodeSelectorTerms"]
            for term in terms:
                term.setdefault("matchExpressions", []).append(
                    NOT_ON_LIQO_VIRTUAL_NODES
                )

            if self.spec.iface is not None:
                for container in pod_spec["containers"]:
                    if container["name"] == "kube-flannel":
                        container["args"].append(f"--iface={self.spec.iface}")

        return obj

    def _gen_net_conf(self) -> dict:
        return {
            "Network": self.settings.cluster_cidr,
            "EnableNFTables": self.spec.enable_nftables,
            "Backend": {"Type": self.spec.backend},
        }


module = FlannelCNI
spec = FlannelCNISpec
