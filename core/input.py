import yaml
import os
from typing import List, Optional, Any
from enum import Enum

from pydantic import BaseModel, Field, model_validator, ValidationError

from core.modules import runtimes, cnis, tools


Runtimes = Enum(
    "Runtimes",
    ((value, value) for value in list(runtimes.keys())),
    type=str,
)
CNIs = Enum(
    "CNIs",
    ((value, value) for value in list(cnis.keys())),
    type=str,
)
Tools = Enum(
    "Tools",
    ((value, value) for value in list(tools.keys())),
    type=str,
)


class RuntimeConfig(BaseModel):
    type: Runtimes
    spec: dict = Field(default_factory=dict)  # TODO: validate


class CommonConfig(BaseModel):
    runtime: Optional[RuntimeConfig] = None
    cni: Optional[CNIs] = None
    nodes: int = 1
    cluster_cidr: str | None = None
    service_cidr: str | None = None
    cache: bool = False


class DeploymentConfig(BaseModel):
    name: str
    replicas: int = 1
    pod_spec: dict = Field(default_factory=dict)  # TODO: validate


class ServiceConfig(BaseModel):
    name: str
    spec: dict = Field(default_factory=dict)  # TODO: validate


class PodConfig(BaseModel):
    name: str
    spec: dict = Field(default_factory=dict)  # TODO: validate


class NamespaceConfig(BaseModel):
    name: str
    deployments: List[DeploymentConfig] = Field(default_factory=list)  # TODO: validate
    services: List[ServiceConfig] = Field(default_factory=list)  # TODO: validate
    pods: List[PodConfig] = Field(default_factory=list)  # TODO: validate


class ClusterConfig(BaseModel):
    name: str

    runtime: RuntimeConfig = None
    cni: CNIs = None
    nodes: int = None
    cluster_cidr: str | None = None
    service_cidr: str | None = None
    cache: bool = None

    namespaces: List[NamespaceConfig] = Field(default_factory=list)


class ToolConfig(BaseModel):
    type: Tools
    spec: dict = Field(default_factory=dict)  # TODO: validate


class RootConfig(BaseModel):
    default: Optional[CommonConfig] = Field(default_factory=CommonConfig)
    clusters: List[ClusterConfig]
    tools: Optional[List[ToolConfig]] = Field(default_factory=list)

    @model_validator(mode="before")
    def merge_defaults(cls, data):
        default_cfg = data.get("default", {})
        for cluster in data.get("clusters", []):
            for field, value in default_cfg.items():
                if field not in cluster:
                    cluster[field] = value
        return data

    @model_validator(mode="after")
    def validate_uniqueness(self):
        # Validate unique cluster names
        cluster_names = set()
        for i, cluster in enumerate(self.clusters):
            if cluster.name in cluster_names:
                raise ValueError(
                    f"Duplicate cluster name found: '{cluster.name}' (at clusters.{i})."
                )
            cluster_names.add(cluster.name)

        return self

    @model_validator(mode="after")
    def validate_compatibility(self):
        # Validate runtime and CNI compatibility
        for i, cluster in enumerate(self.clusters):
            runtime_cls = runtimes[cluster.runtime.type.value]
            if cluster.cni.value not in runtime_cls.SUPPORTED_CNIS:
                raise ValueError(
                    f"Unsupported CNI '{cluster.cni.value}' for runtime '{cluster.runtime.type.value}' "
                    f"in cluster '{cluster.name}' (at clusters.{i}). "
                    f"Supported CNIs are: {runtime_cls.SUPPORTED_CNIS}."
                )
        return self


def format_pydantic_error(err):
    """
    Formats Pydantic location tuple into a readable string.
    Example: ('clusters', 0, 'name') -> 'clusters.0.name'
    """
    loc_path = ".".join(str(x) for x in err["loc"])
    # Remove 'root.' prefix if present for cleaner output
    if loc_path.startswith("root."):
        loc_path = loc_path[5:]
    return f"{loc_path}: {err['msg']}"


def validate_data(raw_data: Any) -> Optional[RootConfig]:
    """Main function to run the validation."""

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
    """Loads and validates a YAML configuration file."""

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
