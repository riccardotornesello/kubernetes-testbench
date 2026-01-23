"""
Base Cluster Module

Defines the abstract base class for all cluster implementations.
This provides a common interface for different cluster runtimes (k3d, kind, etc.)
and ensures consistent behavior across implementations.
"""
from abc import ABC, abstractmethod

from config import CNIEnum


class Cluster(ABC):
    """
    Abstract base class for Kubernetes cluster implementations.
    
    This class defines the interface that all cluster runtime implementations
    must follow. Subclasses should implement the abstract methods to handle
    cluster creation and CNI installation specific to their runtime.
    
    Attributes:
        name: Unique identifier for the cluster
        nodes: Number of nodes in the cluster
        cluster_cidr: CIDR range for pod networking
        service_cidr: CIDR range for service networking
        cni: Container Network Interface plugin to use
    """
    name: str
    nodes: int
    cluster_cidr: str
    service_cidr: str
    cni: CNIEnum

    def __init__(
        self,
        name: str,
        nodes: int,
        cluster_cidr: str,
        service_cidr: str,
        cni: CNIEnum,
    ):
        """
        Initialize a cluster instance.
        
        Args:
            name: Unique cluster name
            nodes: Number of nodes to create
            cluster_cidr: Pod network CIDR
            service_cidr: Service network CIDR
            cni: CNI plugin to install
        """
        self.name = name
        self.nodes = nodes
        self.cluster_cidr = cluster_cidr
        self.service_cidr = service_cidr
        self.cni = cni

    def create(self) -> None:
        """
        Create the cluster and install the CNI plugin.
        
        This is the main entry point for cluster creation. It orchestrates
        the cluster initialization and CNI installation in the correct order.
        """
        self.init_cluster()
        self.install_cni()

    def get_kubeconfig_location(self) -> str:
        """
        Get the file path where the kubeconfig for this cluster is stored.
        
        Returns:
            Path to the kubeconfig file for this cluster
        """
        return f"out/kubeconfigs/{self.name}.yaml"

    @abstractmethod
    def init_cluster(self) -> None:
        """
        Initialize the cluster using the runtime-specific method.
        
        This method must be implemented by subclasses to handle the actual
        cluster creation using tools like k3d, kind, etc.
        
        Raises:
            NotImplementedError: If not implemented by subclass
        """
        raise NotImplementedError("Subclasses must implement this method.")

    @abstractmethod
    def install_cni(self) -> None:
        """
        Install the configured CNI plugin in the cluster.
        
        This method must be implemented by subclasses to handle CNI installation
        in a runtime-specific way, as different runtimes may have different
        requirements or default CNIs.
        
        Raises:
            NotImplementedError: If not implemented by subclass
        """
        raise NotImplementedError("Subclasses must implement this method.")
