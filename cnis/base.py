import ipaddress
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from core.types import Settings
from utils.kubeconfig import get_kubeconfig_location
from utils.kubernetes_utils import wait_for_nodes_ready, wait_for_workloads_ready


class BaseCNISpec(BaseModel):
    """
    Parameters shared by every CNI. Each CNI subclasses this model, adding its
    own parameters and overriding the default `version`.
    """

    model_config = ConfigDict(extra="forbid")

    # CNI version to install (without the leading "v")
    version: str
    # Wait for the CNI (and the nodes) to be ready before going on
    wait: bool = True
    # Seconds to wait for the CNI to become ready
    timeout: int = Field(default=600, gt=0)

    def validate_cluster(
        self,
        cluster_cidr: ipaddress.IPv4Network,
        service_cidr: ipaddress.IPv4Network,
        nodes: int,
    ) -> None:
        """
        Validates the spec against the cluster settings. Raise ValueError on
        error. Called during the configuration validation.
        """
        return


SpecT = TypeVar("SpecT", bound=BaseCNISpec)


class BaseCNI(ABC, Generic[SpecT]):
    # Reference CNI plugins (containernetworking/plugins) the CNI expects to
    # find in /opt/cni/bin on the nodes, without installing them itself.
    REQUIRED_CNI_PLUGINS: list[str] = []

    # Kernel modules the CNI needs, without loading them itself
    REQUIRED_KERNEL_MODULES: list[str] = []

    # DaemonSets/Deployments that must be ready for the CNI to be considered
    # ready, as {namespace: [names]}. None means every workload of the
    # namespace.
    WORKLOADS: dict[str, list[str] | None] = {}

    def __init__(self, cluster_name: str, settings: Settings, spec: SpecT) -> None:
        self.cluster_name = cluster_name
        self.settings = settings
        self.spec = spec

    @property
    def kubeconfig(self) -> str:
        return get_kubeconfig_location(self.cluster_name)

    @property
    def replaces_kube_proxy(self) -> bool:
        """
        Whether the CNI replaces kube-proxy. If so, the runtime creates the
        cluster without kube-proxy.
        """
        return False

    def check_dependencies(self) -> None:
        return

    @abstractmethod
    def install_cni(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")

    def wait_ready(self) -> None:
        print(f"Waiting for CNI '{self.settings.cni}' to be ready")
        for namespace, names in self.WORKLOADS.items():
            wait_for_workloads_ready(
                self.kubeconfig, namespace, self.spec.timeout, names=names
            )
        wait_for_nodes_ready(self.kubeconfig, self.spec.timeout)
        # CoreDNS running means that pod networking works
        wait_for_workloads_ready(
            self.kubeconfig, "kube-system", self.spec.timeout, names=["coredns"]
        )


# Node affinity that keeps the CNI pods off Liqo virtual nodes
NOT_ON_LIQO_VIRTUAL_NODES = {
    "key": "liqo.io/type",
    "operator": "DoesNotExist",
}
