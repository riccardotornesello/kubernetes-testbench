"""
Base CNI Module

Defines the abstract base class for all CNI (Container Network Interface)
implementations. CNI plugins are responsible for configuring network
interfaces in containers, enabling pod-to-pod communication within and
across nodes.
"""
from abc import ABC, abstractmethod


class CNI(ABC):
    """
    Abstract base class for CNI plugin implementations.
    
    This class defines the interface that all CNI implementations must follow.
    Subclasses should implement the install() method to deploy their specific
    CNI solution to a Kubernetes cluster.
    
    Attributes:
        kubeconfig: Path to the kubeconfig file for cluster access
        cidr: CIDR range for pod networking (used to configure IP allocation)
    """
    kubeconfig: str
    cidr: str

    def __init__(self, kubeconfig: str, cidr: str) -> None:
        """
        Initialize a CNI instance.
        
        Args:
            kubeconfig: Path to kubeconfig file for the target cluster
            cidr: Pod network CIDR range
        """
        self.kubeconfig = kubeconfig
        self.cidr = cidr

    @abstractmethod
    def install(self) -> None:
        """
        Install the CNI plugin in the target cluster.
        
        This method must be implemented by subclasses to handle the specific
        installation process for each CNI plugin (applying manifests, configuring
        resources, etc.).
        
        Raises:
            NotImplementedError: If not implemented by subclass
        """
        raise NotImplementedError("Subclasses must implement this method.")
