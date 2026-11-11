import ipaddress
import yaml
import os
from typing import Annotated, List, Literal, Optional, Any, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    create_model,
    field_validator,
    model_validator,
    ValidationError,
)

from core.modules import tool_specs, cni_specs, runtime_specs


def _typed_config_model(name: str, spec_cls: type[BaseModel]) -> type[BaseModel]:
    return create_model(
        f"{spec_cls.__name__}Config",
        type=(Literal[name], ...),
        spec=(spec_cls, Field(default_factory=spec_cls)),
    )


def _type_shorthand(value: Any) -> Any:
    # `cni: flannel` is a shorthand for `cni: {type: flannel}` (same for
    # runtimes and tools)
    if isinstance(value, str):
        return {"type": value}
    return value


def _typed_config(specs: dict[str, type[BaseModel]]) -> Any:
    """
    `{type: <name>, spec: {...}}`, where `spec` is validated against the spec
    model exported by the <name> module. A plain string is the type alone.
    """
    return Annotated[
        Union[tuple(_typed_config_model(n, s) for n, s in specs.items())],
        Field(discriminator="type"),
        BeforeValidator(_type_shorthand),
    ]


# `runtime: {type: <name>, spec: {...}}`, spec from runtimes/<name>.py
RuntimeConfig = _typed_config(runtime_specs)
# `cni: {type: <name>, spec: {...}}`, spec from cnis/<name>.py
CNIConfig = _typed_config(cni_specs)
# `- {type: <name>, spec: {...}}` in `tools`, spec from tools/<name>.py
ToolConfig = _typed_config(tool_specs)

DEFAULT_CNI = "flannel"
DEFAULT_CLUSTER_CIDR = "10.244.0.0/16"
DEFAULT_SERVICE_CIDR = "10.96.0.0/16"


def _validate_ipv4_network(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(ipaddress.IPv4Network(value, strict=True))
    except ValueError as e:
        raise ValueError(f"invalid IPv4 CIDR '{value}': {e}") from e


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


class CommonConfig(BaseModel):
    """`default`: every cluster field but `name`, copied into each cluster."""

    model_config = ConfigDict(extra="forbid")

    runtime: Optional[RuntimeConfig] = None
    cni: Optional[CNIConfig] = None
    nodes: int = Field(default=1, ge=1)
    cluster_cidr: str | None = None
    service_cidr: str | None = None
    namespaces: Optional[List[NamespaceConfig]] = None

    _validate_cidrs = field_validator("cluster_cidr", "service_cidr")(
        _validate_ipv4_network
    )


class ClusterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str

    runtime: RuntimeConfig
    cni: CNIConfig = Field(default=DEFAULT_CNI, validate_default=True)
    nodes: int = Field(default=1, ge=1)
    cluster_cidr: str = DEFAULT_CLUSTER_CIDR
    service_cidr: str = DEFAULT_SERVICE_CIDR

    namespaces: List[NamespaceConfig] = Field(default_factory=list)

    _validate_cidrs = field_validator("cluster_cidr", "service_cidr")(
        _validate_ipv4_network
    )

    @model_validator(mode="after")
    def validate_network(self):
        cluster_cidr = ipaddress.IPv4Network(self.cluster_cidr)
        service_cidr = ipaddress.IPv4Network(self.service_cidr)

        if cluster_cidr.overlaps(service_cidr):
            raise ValueError(
                f"cluster_cidr {cluster_cidr} overlaps service_cidr {service_cidr}"
            )

        # Node pod CIDRs are /24 (default --node-cidr-mask-size)
        if (
            cluster_cidr.prefixlen > 24
            or 2 ** (24 - cluster_cidr.prefixlen) < self.nodes
        ):
            raise ValueError(
                f"cluster_cidr {cluster_cidr} does not contain a /24 "
                f"for each of the {self.nodes} nodes"
            )

        self.cni.spec.validate_cluster(cluster_cidr, service_cidr, self.nodes)
        return self


class RootConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: Optional[CommonConfig] = None
    clusters: List[ClusterConfig]
    tools: List[ToolConfig] = Field(default_factory=list)

    @model_validator(mode="before")
    def merge_defaults(cls, data):
        # Each field of `default` is copied, as is, into the clusters that do
        # not define it (shallow merge: a cluster `cni` replaces the default one)
        if not isinstance(data, dict):
            return data
        default_cfg = data.get("default") or {}
        if not isinstance(default_cfg, dict):
            return data
        for cluster in data.get("clusters") or []:
            if not isinstance(cluster, dict):
                continue
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
    def validate_tools(self):
        cluster_names = [cluster.name for cluster in self.clusters]
        for i, tool in enumerate(self.tools):
            try:
                tool.spec.validate_clusters(cluster_names)
            except ValueError as e:
                raise ValueError(f"tools.{i} ({tool.type}): {e}") from e
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
    return f"{loc_path}: {err['msg']}" if loc_path else err["msg"]


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
