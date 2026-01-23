"""
Base Tool Module

Defines the abstract base class for all tool integrations.
Tools are additional software components that can be installed and configured
across clusters, such as service meshes, networking solutions, or monitoring systems.
"""
from abc import ABC, abstractmethod


class Tool(ABC):
    """
    Abstract base class for tool implementations.
    
    This class defines the interface that all tool integrations must follow.
    Subclasses should implement the install() method to deploy and configure
    their specific tool across one or more clusters.
    
    Examples of tools include:
    - Liqo: Multi-cluster networking
    - Service meshes (Istio, Linkerd)
    - Monitoring solutions (Prometheus, Grafana)
    """
    
    @abstractmethod
    def install(self) -> None:
        """
        Install and configure the tool.
        
        This method must be implemented by subclasses to handle the specific
        installation and configuration process for each tool. This may include:
        - Installing Helm charts or manifests
        - Configuring resources across multiple clusters
        - Setting up connections or peerings between clusters
        
        Raises:
            NotImplementedError: If not implemented by subclass
        """
        raise NotImplementedError("Subclasses must implement this method.")
