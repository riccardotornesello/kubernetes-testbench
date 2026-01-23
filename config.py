"""
Configuration Module for Kubernetes Testbench

This module handles configuration parsing, validation, and data models
for the testbench. It uses Pydantic for schema validation and supports
inheritance of default values across clusters.

The configuration hierarchy:
1. Default values (optional) apply to all clusters
2. Individual cluster values override defaults
3. Validation ensures consistency and catches errors early
"""
import yaml
import os
from enum import Enum
from typing import List, Optional, Any, Tuple
from pydantic import BaseModel, Field, model_validator, ValidationError


class RuntimeEnum(str, Enum):
    """Supported cluster runtime environments."""
    k3d = "k3d"


class CNIEnum(str, Enum):
    """Supported Container Network Interface (CNI) plugins."""
    calico = "calico"
    flannel = "flannel"
    cilium = "cilium"


class LiqoInstallationConfig(BaseModel):
    """Configuration for Liqo installation in a specific cluster."""
    cluster: str
    version: Optional[str] = None


class LiqoConfig(BaseModel):
    """Configuration for Liqo multi-cluster networking tool."""
    installations: List[LiqoInstallationConfig] = Field(default_factory=list)
    peerings: List[Tuple[str, str]] = Field(default_factory=list)


class ToolsConfig(BaseModel):
    """Configuration for additional tools to install."""
    liqo: Optional[LiqoConfig] = None


class CommonConfig(BaseModel):
    """
    Default configuration values that can be inherited by clusters.
    
    These values provide sensible defaults for cluster creation and can be
    overridden on a per-cluster basis.
    """
    runtime: RuntimeEnum = RuntimeEnum.k3d
    cni: CNIEnum = CNIEnum.calico
    nodes: int = 1
    cluster_cidr: str = "10.200.0.0/16"
    service_cidr: str = "10.71.0.0/16"


class ClusterConfig(BaseModel):
    """
    Configuration for an individual Kubernetes cluster.
    
    All fields except 'name' are optional and will inherit from the 'default'
    section if not specified.
    """
    name: str

    runtime: Optional[RuntimeEnum] = None
    cni: Optional[CNIEnum] = None
    nodes: Optional[int] = None
    cluster_cidr: Optional[str] = None
    service_cidr: Optional[str] = None


class RootConfig(BaseModel):
    """
    Root configuration model representing the entire configuration file.
    
    This model includes validators to:
    - Merge default values into cluster configurations
    - Ensure cluster names are unique
    - Validate cross-cluster relationships
    """
    default: Optional[CommonConfig] = Field(default_factory=CommonConfig)
    clusters: List[ClusterConfig]
    tools: Optional[ToolsConfig] = Field(default_factory=ToolsConfig)

    @model_validator(mode="before")
    @classmethod
    def merge_defaults_into_clusters(cls, data):
        """
        PRE-VALIDATION HOOK.
        Merges values from the 'default' section into each cluster entry
        if the cluster doesn't specify them.
        
        This allows users to define common settings once and have them
        automatically applied to all clusters, while still allowing
        per-cluster overrides.
        """
        if not isinstance(data, dict):
            return data  # Let Pydantic handle the type error

        defaults = data.get("default", {})
        clusters = data.get("clusters", [])

        if not isinstance(clusters, list):
            return data  # Let Pydantic handle the type error

        # We modify the raw dictionary data before Pydantic creates the objects.
        # This ensures that when ClusterConfig is instantiated, it has all the data.
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue

            # If missing in cluster AND present in default -> Copy from default
            inheritable_fields = [
                "runtime",
                "nodes",
                "cni",
                "cluster_cidr",
                "service_cidr",
            ]

            for field in inheritable_fields:
                if field not in cluster and field in defaults:
                    cluster[field] = defaults[field]

        return data

    @model_validator(mode="after")
    def validate_global_logic(self):
        """
        POST-VALIDATION HOOK.
        Validates cross-cluster logic (uniqueness).
        
        Ensures that all cluster names are unique to prevent conflicts
        during cluster creation and tool installation.
        """
        cluster_names = set()

        # Check name uniqueness
        for i, cluster in enumerate(self.clusters):
            if cluster.name in cluster_names:
                raise ValueError(
                    f"Duplicate cluster name found: '{cluster.name}' (at clusters.{i})."
                )
            cluster_names.add(cluster.name)

        return self


def format_pydantic_error(err):
    """
    Formats Pydantic location tuple into a readable string.
    
    Args:
        err: Pydantic error dictionary
        
    Returns:
        Human-readable error message
        
    Example:
        ('clusters', 0, 'name') -> 'clusters.0.name'
    """
    loc_path = ".".join(str(x) for x in err["loc"])
    # Remove 'root.' prefix if present for cleaner output
    if loc_path.startswith("root."):
        loc_path = loc_path[5:]
    return f"{loc_path}: {err['msg']}"


def validate_data(raw_data: Any) -> Optional[RootConfig]:
    """
    Validate raw configuration data against the schema.
    
    Args:
        raw_data: Parsed YAML data (typically a dictionary)
        
    Returns:
        Validated RootConfig object, or None if validation fails
    """
    if raw_data is None:
        print("❌ File is empty.")
        return None

    try:
        # Trigger Validation
        cfg = RootConfig(**raw_data)
        print("✅ Validation Successful!")

    except ValidationError as e:
        print("❌ Validation Failed. Errors found:")
        for err in e.errors():
            print(f" - {format_pydantic_error(err)}")
        return None

    return cfg


def validate_config_file(file_path: str) -> Optional[RootConfig]:
    """
    Load and validate a YAML configuration file.
    
    Args:
        file_path: Path to the YAML configuration file
        
    Returns:
        Validated RootConfig object, or None if file doesn't exist or validation fails
    """
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return None

    with open(file_path, "r") as f:
        try:
            raw_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"❌ YAML Syntax Error: {e}")
            return None

    return validate_data(raw_data)


if __name__ == "__main__":
    # Example usage
    # Generating a test file that exercises the inheritance and errors
    test_yaml = """
default:
  nodes: 2            # Will be inherited by cluster-A

clusters:
  - name: "cluster-A"
    runtime: "docker"
    cni: "calico"
    # nodes inherited (2)

  - name: "cluster-B"
    # runtime MISSING (error)
    cni: "flannel"
    nodes: "not-a-number" # Type Error

  - name: "cluster-A" # Duplicate Name Error
    runtime: "containerd"
    cni: "cilium"
"""

    raw_data = yaml.safe_load(test_yaml)
    validate_data(raw_data)
