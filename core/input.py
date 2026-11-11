"""Models and validation of the YAML configuration file."""

import ipaddress
import os
import re
from typing import Annotated, List, Literal, Optional, Any, Tuple, Union

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    create_model,
    field_validator,
    model_validator,
    ValidationError,
)

from core.log import get_logger
from core.settings import DEFAULT_NETWORK_POOL
from core.modules import tool_specs, cni_specs, runtime_specs
from tools.base import check_known_clusters
from utils.kubernetes_models import (
    KubernetesValidationError,
    model_name_for,
    to_model,
)

log = get_logger(__name__)


def _typed_config_model(name: str, spec_cls: type[BaseModel]) -> type[BaseModel]:
    """`{type: <name>, spec: <spec_cls>}` model of a component."""
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
    """Normalizes an IPv4 CIDR, rejecting host bits."""
    if value is None:
        return None
    try:
        return str(ipaddress.IPv4Network(value, strict=True))
    except ValueError as e:
        raise ValueError(f"invalid IPv4 CIDR '{value}': {e}") from e


def _kubernetes_model(type_name: str) -> Any:
    """
    A camelCase mapping (as in a Kubernetes manifest) validated against, and
    converted into, the model `type_name` of the official Kubernetes client
    (e.g. `V1PodSpec`): unknown fields, wrong types and missing required
    fields are rejected.
    """
    return Annotated[Any, BeforeValidator(lambda value: to_model(value, type_name))]


# RFC 1123 label (namespaces), DNS-1035 label (services), RFC 1123 subdomain
# (pods, deployments): the rules applied by the API server to the names
_DNS1123_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_DNS1035_LABEL = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")


def _validate_dns1123_label(value: str) -> str:
    """Validates a namespace or cluster name."""
    if len(value) > 63 or not _DNS1123_LABEL.match(value):
        raise ValueError(
            f"'{value}' is not a valid RFC 1123 label: at most 63 lowercase "
            "alphanumeric characters or '-', starting and ending with an "
            "alphanumeric character"
        )
    return value


def _validate_dns1035_label(value: str) -> str:
    """Validates a service name."""
    if len(value) > 63 or not _DNS1035_LABEL.match(value):
        raise ValueError(
            f"'{value}' is not a valid DNS-1035 label: at most 63 lowercase "
            "alphanumeric characters or '-', starting with a letter and ending "
            "with an alphanumeric character"
        )
    return value


def _validate_dns1123_subdomain(value: str) -> str:
    """Validates a pod or deployment name."""
    if len(value) > 253 or not all(
        _DNS1123_LABEL.match(part) for part in value.split(".")
    ):
        raise ValueError(
            f"'{value}' is not a valid RFC 1123 subdomain: at most 253 "
            "lowercase alphanumeric characters, '-' or '.', starting and "
            "ending with an alphanumeric character"
        )
    return value


Dns1123Label = Annotated[str, AfterValidator(_validate_dns1123_label)]
Dns1035Label = Annotated[str, AfterValidator(_validate_dns1035_label)]
Dns1123Subdomain = Annotated[str, AfterValidator(_validate_dns1123_subdomain)]


def _ensure_unique(items: list, what: str) -> None:
    """Raises if two `items` share a name."""
    seen = set()
    for i, item in enumerate(items):
        if item.name in seen:
            raise ValueError(f"duplicate {what} name '{item.name}' (at {i})")
        seen.add(item.name)


class DeploymentConfig(BaseModel):
    """Deployment of `replicas` pods labeled `app: <name>`."""

    model_config = ConfigDict(extra="forbid")

    name: Dns1123Subdomain
    replicas: int = Field(default=1, ge=0)
    # Pod template spec; labels `app: <name>` select the pods
    pod_spec: _kubernetes_model("V1PodSpec")


class ServiceConfig(BaseModel):
    """Service of a namespace."""

    model_config = ConfigDict(extra="forbid")

    name: Dns1035Label
    spec: _kubernetes_model("V1ServiceSpec") = Field(
        default_factory=dict, validate_default=True
    )


class PodConfig(BaseModel):
    """Standalone pod labeled `app: <name>`."""

    model_config = ConfigDict(extra="forbid")

    name: Dns1123Subdomain
    spec: _kubernetes_model("V1PodSpec")


class ManifestConfig(BaseModel):
    """
    A (multi-document) YAML manifest, applied with server-side apply.
    `manifests: [path]` is a shorthand for `manifests: [{path: path}]`; a
    relative path is relative to the directory of the configuration file.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    # Objects of the manifest, loaded and checked at validation time
    objects: List[dict] = Field(default_factory=list, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def load(cls, data: Any, info: ValidationInfo) -> Any:
        """Resolves the path and loads the objects of the manifest."""
        if isinstance(data, str):
            data = {"path": data}
        if not isinstance(data, dict) or not isinstance(data.get("path"), str):
            return data

        base_dir = (info.context or {}).get("base_dir") or os.getcwd()
        path = os.path.normpath(
            os.path.join(base_dir, os.path.expanduser(data["path"]))
        )
        return {**data, "path": path, "objects": load_manifest_file(path)}


def load_manifest_file(path: str) -> list[dict]:
    """
    Loads a YAML manifest and checks its objects: `apiVersion`, `kind` and
    `metadata.name` are required, and built-in resources are validated
    against the models of the Kubernetes client.
    """
    if not os.path.isfile(path):
        raise ValueError(f"manifest file not found: {path}")
    try:
        with open(path, "r") as f:
            documents = list(yaml.safe_load_all(f))
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"cannot read manifest {path}: {e}") from e

    objects = []
    errors = []
    for i, obj in enumerate(documents):
        if obj is None:
            continue
        where = f"{path} (document {i})"
        if not isinstance(obj, dict):
            errors.append(f"{where}: expected a mapping, got {type(obj).__name__}")
            continue
        missing = [f for f in ("apiVersion", "kind") if not isinstance(obj.get(f), str)]
        if not isinstance((obj.get("metadata") or {}).get("name"), str):
            missing.append("metadata.name")
        if missing:
            errors.append(f"{where}: missing {', '.join(missing)}")
            continue
        where = f"{path} ({obj['kind']} {obj['metadata']['name']})"
        if obj["kind"].endswith("List"):
            errors.append(f"{where}: List objects are not supported")
            continue

        model = model_name_for(obj["apiVersion"], obj["kind"])
        if model is not None:
            try:
                to_model(obj, model)
            except KubernetesValidationError as e:
                errors.extend(f"{where}: {err}" for err in e.errors)
                continue
        objects.append(obj)

    if errors:
        raise KubernetesValidationError(errors)
    if not objects:
        raise ValueError(f"manifest {path} contains no objects")
    return objects


class NamespaceConfig(BaseModel):
    """Namespace and the workloads created in it."""

    model_config = ConfigDict(extra="forbid")

    name: Dns1123Label
    # Applied before the deployments, services and pods; objects without a
    # namespace are created in this one
    manifests: List[ManifestConfig] = Field(default_factory=list)
    deployments: List[DeploymentConfig] = Field(default_factory=list)
    services: List[ServiceConfig] = Field(default_factory=list)
    pods: List[PodConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_uniqueness(self):
        """Names are unique per kind."""
        _ensure_unique(self.deployments, "deployment")
        _ensure_unique(self.services, "service")
        _ensure_unique(self.pods, "pod")
        return self


class ClusterConfig(BaseModel):
    """A cluster: runtime, CNI, network and workloads."""

    model_config = ConfigDict(extra="forbid")

    # Used in file names and in the runtime commands (DNS label)
    name: Dns1123Label

    runtime: RuntimeConfig
    cni: CNIConfig = Field(default=DEFAULT_CNI, validate_default=True)
    nodes: int = Field(default=1, ge=1)
    cluster_cidr: str = DEFAULT_CLUSTER_CIDR
    service_cidr: str = DEFAULT_SERVICE_CIDR

    # Applied after the namespaces are created, before their workloads
    manifests: List[ManifestConfig] = Field(default_factory=list)
    namespaces: List[NamespaceConfig] = Field(default_factory=list)

    _validate_cidrs = field_validator("cluster_cidr", "service_cidr")(
        _validate_ipv4_network
    )

    @model_validator(mode="after")
    def validate_network(self):
        """CIDRs do not overlap, fit the nodes and suit the CNI."""
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

    @model_validator(mode="after")
    def validate_namespaces(self):
        """Namespace names are unique."""
        _ensure_unique(self.namespaces, "namespace")
        return self


class NetworkConfig(BaseModel):
    """
    Networks of the nodes: every cluster has a network of its own, and the
    host routes between them (without NAT) according to `links`.
    """

    model_config = ConfigDict(extra="forbid")

    # Address pool of the networks: the first /24 is reserved to the shared
    # infrastructure (registry proxy), then one /24 per cluster, in
    # configuration order
    cidr: str = DEFAULT_NETWORK_POOL
    # Pairs of clusters whose nodes reach each other (both directions).
    # Omitted: every cluster reaches every other one. An empty list isolates
    # every cluster.
    links: Optional[List[Tuple[str, str]]] = None

    _validate_cidr = field_validator("cidr")(_validate_ipv4_network)

    @field_validator("links")
    @classmethod
    def _validate_links(
        cls, links: Optional[List[Tuple[str, str]]]
    ) -> Optional[List[Tuple[str, str]]]:
        for a, b in links or []:
            if a == b:
                raise ValueError(f"link of cluster '{a}' with itself")
        return links


class RootConfig(BaseModel):
    """The whole configuration file."""

    model_config = ConfigDict(extra="forbid")

    # Cluster fields (all but `name`) copied into the clusters that do not set
    # them. Validated on its own by `validate_data`.
    default: Optional[dict] = None
    clusters: List[ClusterConfig]
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    tools: List[ToolConfig] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def merge_defaults(cls, data: Any) -> Any:
        # Each field of `default` is copied, as is, into the clusters that do
        # not define it (shallow merge: a cluster `cni` replaces the default one)
        if not isinstance(data, dict):
            return data
        default_cfg = data.get("default") or {}
        clusters = data.get("clusters")
        if not isinstance(default_cfg, dict) or not isinstance(clusters, list):
            return data
        return {
            **data,
            "clusters": [
                {**default_cfg, **cluster} if isinstance(cluster, dict) else cluster
                for cluster in clusters
            ],
        }

    @model_validator(mode="after")
    def validate_uniqueness(self):
        """Cluster names are unique."""
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
    def validate_network(self):
        """The pool fits the clusters and does not overlap their CIDRs."""
        pool = ipaddress.IPv4Network(self.network.cidr)
        # One /24 for the infrastructure, one per cluster
        needed = len(self.clusters) + 1
        if pool.prefixlen > 24 or 2 ** (24 - pool.prefixlen) < needed:
            raise ValueError(
                f"network.cidr {pool} does not contain {needed} /24 subnets "
                f"(one for the infrastructure, one per cluster)"
            )
        for cluster in self.clusters:
            for field in ("cluster_cidr", "service_cidr"):
                cidr = ipaddress.IPv4Network(getattr(cluster, field))
                if pool.overlaps(cidr):
                    raise ValueError(
                        f"network.cidr {pool} overlaps the {field} {cidr} of "
                        f"cluster '{cluster.name}'"
                    )
        names = [cluster.name for cluster in self.clusters]
        for i, link in enumerate(self.network.links or []):
            try:
                check_known_clusters(list(link), names)
            except ValueError as e:
                raise ValueError(f"network.links.{i}: {e}") from e
        return self

    @model_validator(mode="after")
    def validate_tools(self):
        """Each tool spec is checked against the cluster names."""
        cluster_names = [cluster.name for cluster in self.clusters]
        for i, tool in enumerate(self.tools):
            try:
                tool.spec.validate_clusters(cluster_names)
            except ValueError as e:
                raise ValueError(f"tools.{i} ({tool.type}): {e}") from e
        return self


def format_pydantic_error(err) -> list[str]:
    """
    Formats a Pydantic error as readable lines, prefixed by its location.
    Example: ('clusters', 0, 'name') -> 'clusters.0.name: ...'. Kubernetes
    validation errors give one line per error, e.g.
    'clusters.0.namespaces.0.pods.0.spec: containers.0.name: field required'.
    """
    loc_path = ".".join(str(x) for x in err["loc"])
    # Remove 'root.' prefix if present for cleaner output
    if loc_path.startswith("root."):
        loc_path = loc_path[5:]

    exc = (err.get("ctx") or {}).get("error")
    if isinstance(exc, KubernetesValidationError):
        messages = exc.errors
    elif isinstance(exc, ValueError):
        messages = [str(exc)]
    else:
        messages = [err["msg"]]

    return [f"{loc_path}: {msg}" if loc_path else msg for msg in messages]


def _validate_default(default_cfg: dict, context: dict) -> list[dict]:
    """
    Validates `default` as a partial cluster: only the errors on the fields
    it sets are kept (the missing ones may be set by every cluster, and the
    checks across fields depend on the cluster).
    """
    errors = []
    if "name" in default_cfg:
        errors.append({"loc": ("default", "name"), "msg": "not allowed in default"})
    try:
        ClusterConfig.model_validate(
            {**default_cfg, "name": "default"}, context=context
        )
    except ValidationError as e:
        errors.extend(
            {**err, "loc": ("default", *err["loc"])}
            for err in e.errors()
            if err["loc"] and err["loc"][0] in default_cfg and err["loc"][0] != "name"
        )
    return errors


def _inherited_error(err: dict, raw_data: dict, failed_defaults: set) -> bool:
    """
    Whether `err` is on a cluster field inherited from `default` that is
    already reported as a `default` error.
    """
    loc = err["loc"]
    if len(loc) < 3 or loc[0] != "clusters" or loc[2] not in failed_defaults:
        return False
    clusters = raw_data.get("clusters")
    if not isinstance(clusters, list) or not isinstance(loc[1], int):
        return False
    cluster = clusters[loc[1]] if loc[1] < len(clusters) else None
    return isinstance(cluster, dict) and loc[2] not in cluster


def validate_data(raw_data: Any, base_dir: str | None = None) -> Optional[RootConfig]:
    """
    Main function to run the validation. `base_dir` is the directory relative
    manifest paths are resolved against (default: the working directory).

    An error in `default` is reported once, as `default.<field>`, and not
    again for every cluster inheriting the field.
    """

    if raw_data is None:
        log.error("Configuration is empty")
        return None
    if not isinstance(raw_data, dict):
        log.error("Configuration must be a mapping", found=type(raw_data).__name__)
        return None

    context = {"base_dir": base_dir or os.getcwd()}
    default_cfg = raw_data.get("default")
    errors = (
        _validate_default(default_cfg, context) if isinstance(default_cfg, dict) else []
    )
    failed_defaults = {err["loc"][1] for err in errors}

    cfg = None
    try:
        cfg = RootConfig.model_validate(raw_data, context=context)
    except ValidationError as e:
        errors.extend(
            err
            for err in e.errors()
            if not _inherited_error(err, raw_data, failed_defaults)
        )

    if errors:
        lines = [line for err in errors for line in format_pydantic_error(err)]
        log.error("Configuration validation failed", errors=len(lines))
        for line in lines:
            log.error(line)
        return None

    log.info("Configuration validated")

    return cfg


def validate_config_file(file_path: str) -> Optional[RootConfig]:
    """Loads and validates a YAML configuration file."""

    if not os.path.exists(file_path):
        log.error("Configuration file not found", path=file_path)
        return None

    with open(file_path, "r") as f:
        try:
            raw_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            log.error("Invalid YAML", path=file_path, error=str(e))
            return None

    return validate_data(raw_data, base_dir=os.path.dirname(os.path.abspath(file_path)))
