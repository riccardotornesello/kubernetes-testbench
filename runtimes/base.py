"""Base class of the runtimes and the generic node options."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

from core.types import Settings
from cnis.base import BaseCNI


class BaseRuntimeSpec(BaseModel):
    """
    Parameters of a runtime. Each runtime subclasses this model, adding its
    own parameters. Unknown parameters are rejected.
    """

    model_config = ConfigDict(extra="forbid")


SpecT = TypeVar("SpecT", bound=BaseRuntimeSpec)

# Version of the reference CNI plugins (containernetworking/plugins) copied
# into the nodes when the CNI requires them and the node image lacks them.
CNI_PLUGINS_VERSION = "1.9.1"

# Label set on every node: the i-th node (0 = control plane) gets
# `tier=worker-<i>`, so that workloads can target a specific node.
NODE_TIER_LABEL = "tier"


def node_tier(index: int) -> str:
    """Value of the tier label of the `index`-th node."""
    return f"worker-{index}"


# Path of the proxy CA inside the nodes. Go programs (containerd, cri-o,
# docker) load every file of /etc/ssl/certs as a trusted CA.
NODE_PROXY_CA_PATH = "/etc/ssl/certs/node-proxy-ca.pem"


@dataclass
class NodeProxy:
    """
    HTTP(S) proxy the container runtime of the nodes pulls images through
    (e.g. a caching registry proxy), trusting its CA.
    """

    # URL of the proxy, on the infrastructure network (routed to every
    # cluster network by the host)
    address: str
    # Host path of the proxy CA certificate (PEM)
    ca_file: str
    # Path, relative to the proxy URL, from which the nodes download the CA
    ca_url_path: str = "/ca.crt"
    # Destinations reached without the proxy. The runtime adds the cluster
    # pod and service CIDRs.
    no_proxy: list[str] = field(default_factory=list)


class BaseRuntime(ABC, Generic[SpecT]):
    """
    A runtime creates a cluster of `settings.nodes` nodes (one control plane,
    the others workers), with the given pod/service CIDRs, and every node
    labeled `tier=worker-<i>`.

    The cluster is always created WITHOUT its built-in CNI (and without
    kube-proxy if the CNI replaces it): the CNI is always installed by the
    testbench, so that every CNI receives the same parameters on every runtime.

    The kubeconfig of the cluster (and only of this cluster) must be written to
    utils.kubeconfig.get_kubeconfig_location(name) by init_cluster.

    Runtimes contain no tool-specific logic: tools customize the cluster
    through generic options set before init_cluster (e.g. set_node_proxy).
    """

    def __init__(
        self,
        name: str,
        settings: Settings,
        spec: SpecT,
        cni: BaseCNI,
    ):
        self.name = name
        self.settings = settings
        self.spec = spec
        self.cni = cni
        self.node_proxy: NodeProxy | None = None

    @property
    def network_type(self) -> Literal["docker", "libvirt"]:
        """
        Type of the network of the nodes (`settings.network`, created by the
        testbench before init_cluster): a docker bridge network for container
        nodes, a libvirt network for VM nodes.
        """
        return "docker"

    @property
    def provides_load_balancer(self) -> bool:
        """
        Whether the cluster implements Services of type LoadBalancer (i.e. they
        get an external address reachable from the other clusters).
        """
        return False

    def set_node_proxy(self, proxy: NodeProxy) -> None:
        """
        Makes the container runtime of the nodes pull images through `proxy`.
        Must be called before init_cluster (e.g. from the tool
        `pre_cluster_init` hook), which applies it.
        """
        self.node_proxy = proxy

    def node_no_proxy(self) -> str:
        """NO_PROXY value for the nodes, including the cluster CIDRs."""
        assert self.node_proxy is not None
        return ",".join(
            [
                *self.node_proxy.no_proxy,
                self.settings.cluster_cidr,
                self.settings.service_cidr,
            ]
        )

    def node_proxy_setup_script(self, service: str) -> str:
        """
        Shell script (run as root on a systemd node) that makes the given
        container runtime service use the node proxy: it downloads and trusts
        the proxy CA, sets the proxy environment of the service and restarts it.
        """
        assert self.node_proxy is not None
        address = self.node_proxy.address
        dropin_dir = f"/etc/systemd/system/{service}.service.d"
        lines = [
            "set -e",
            f"curl -fsSL {address}{self.node_proxy.ca_url_path}"
            f" -o {NODE_PROXY_CA_PATH}",
            f"mkdir -p {dropin_dir}",
            f"cat > {dropin_dir}/http-proxy.conf <<'END'",
            "[Service]",
            f'Environment="HTTP_PROXY={address}"',
            f'Environment="HTTPS_PROXY={address}"',
            f'Environment="NO_PROXY={self.node_no_proxy()}"',
            "END",
            "systemctl daemon-reload",
            f"systemctl restart {service}",
        ]
        return "\n".join(lines) + "\n"

    def check_dependencies(self) -> None:
        """Raises if a dependency of the runtime (e.g. its CLI) is missing."""
        pass

    @abstractmethod
    def init_cluster(self) -> None:
        """Creates the cluster and writes its kubeconfig."""
        raise NotImplementedError("Subclasses must implement this method.")

    def install_cni_plugins(self, plugins: list[str]) -> None:
        """
        Makes sure the given reference CNI plugins are in /opt/cni/bin on
        every node. Runtimes whose node image already ships them do nothing.
        """
        pass

    def load_kernel_modules(self, modules: list[str]) -> None:
        """
        Makes sure the given kernel modules are loaded on every node.
        VM-based runtimes, whose nodes already load them, do nothing.
        """
        pass

    def install_cni(self) -> None:
        """Prepares the nodes, installs the CNI and waits for it if requested."""
        self.load_kernel_modules(self.cni.REQUIRED_KERNEL_MODULES)
        self.install_cni_plugins(self.cni.REQUIRED_CNI_PLUGINS)
        self.cni.install_cni()
        if self.cni.spec.wait:
            self.cni.wait_ready()

    @abstractmethod
    def cleanup(self) -> None:
        """Deletes the cluster. Must not fail if the cluster does not exist."""
        raise NotImplementedError("Subclasses must implement this method.")

    @abstractmethod
    def get_api_server_address(self) -> str:
        """
        URL of the API server reachable from the nodes of the OTHER clusters
        (i.e. the node address on its network, not the port forwarded on the
        host).
        """
        raise NotImplementedError("Subclasses must implement this method.")
