"""Base class of the tools and their hooks."""

from abc import ABC
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from core.types import Settings
from runtimes.base import BaseRuntime
from cnis.base import BaseCNI


class BaseToolSpec(BaseModel):
    """
    Parameters of a tool. Each tool subclasses this model, adding its own
    parameters. Unknown parameters are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    def validate_clusters(self, clusters: list[str]) -> None:
        """
        Validates the spec against the names of the configured clusters (e.g.
        the clusters it refers to must exist). Raise ValueError on error.
        Called during the configuration validation.
        """
        return


def check_known_clusters(referenced: list[str], clusters: list[str]) -> None:
    """Raises ValueError if any of `referenced` is not a configured cluster."""
    unknown = sorted(set(referenced) - set(clusters))
    if unknown:
        raise ValueError(f"unknown clusters {unknown} (configured: {clusters})")


SpecT = TypeVar("SpecT", bound=BaseToolSpec)

# Runtime and CNI instances, whatever their spec type
AnyRuntime = BaseRuntime[Any]
AnyCNI = BaseCNI[Any]


class BaseTool(ABC, Generic[SpecT]):
    """
    Base class for tools (e.g. Liqo) installed on top of the clusters.

    Hook execution order (see main.py):

        before_all_operations(...)    -> ONCE in total, before any cluster
        for each cluster (sequentially, in config order):
            pre_cluster_init(...)     -> once PER CLUSTER
            <runtime creates cluster>
            post_cluster_init(...)    -> once PER CLUSTER
            pre_cni_install(...)      -> once PER CLUSTER
            <runtime installs CNI>
            post_cni_install(...)     -> once PER CLUSTER
        after_all_operations(...)     -> ONCE in total, after all clusters are up
        <namespaces/workloads from the config are created>

    Every tool instance receives every hook, for every cluster: a tool that
    should only act on some clusters must filter by itself (see LiqoTool).
    Within each hook, tools are invoked in the order they appear in `tools:`.
    """

    def __init__(self, spec: SpecT) -> None:
        self.spec: SpecT = spec

    def check_dependencies(self) -> None:
        # Raise if a dependency (e.g. a CLI) of the tool is missing
        pass

    def before_all_operations(
        self,
        all_settings: dict[str, Settings],
        all_runtimes: dict[str, AnyRuntime],
        all_cnis: dict[str, AnyCNI],
    ) -> None:
        # Called ONCE in total, after the old clusters are deleted and before
        # any cluster is created. Use it to set up shared infrastructure
        # (e.g. the registry proxy). Same arguments as after_all_operations:
        # the settings, runtime and CNI of every cluster, keyed by cluster name.
        pass

    def pre_cluster_init(
        self,
        settings: Settings,
        runtime: AnyRuntime,
        cni: AnyCNI,
    ) -> None:
        # Called once PER CLUSTER, before the runtime creates the cluster.
        # The cluster does not exist yet: no kubeconfig is available.
        pass

    def post_cluster_init(
        self,
        settings: Settings,
        runtime: AnyRuntime,
        cni: AnyCNI,
    ) -> None:
        # Called once PER CLUSTER, right after the cluster is created and its
        # kubeconfig is written, before the CNI is installed.
        pass

    def pre_cni_install(
        self,
        settings: Settings,
        runtime: AnyRuntime,
        cni: AnyCNI,
    ) -> None:
        # Called once PER CLUSTER, right after post_cluster_init and before
        # the CNI installation. Tools that need a custom CNI configuration
        # register it here, through cni.add_node_selector_requirement (generic)
        # or cni.add_patch (CNI-specific configuration format).
        pass

    def post_cni_install(
        self,
        settings: Settings,
        runtime: AnyRuntime,
        cni: AnyCNI,
    ) -> None:
        # Called once PER CLUSTER, after the CNI is installed.
        # Last per-cluster hook: the cluster is considered ready here.
        pass

    def after_all_operations(
        self,
        all_settings: dict[str, Settings],
        all_runtimes: dict[str, AnyRuntime],
        all_cnis: dict[str, AnyCNI],
    ) -> None:
        # Called ONCE in total, after every cluster went through all the
        # per-cluster hooks. Use it for cross-cluster operations (e.g. peering).
        # Runs BEFORE the namespaces/workloads declared in the config are
        # created.
        pass
