from abc import ABC
from typing import Generic, TypeVar, Type, Dict

from pydantic import BaseModel

from core.types import Settings
from runtimes.base import BaseRuntime
from cnis.base import BaseCNI

SpecT = TypeVar("SpecT", bound=BaseModel)


class BaseTool(ABC, Generic[SpecT]):
    """
    Base class for tools (e.g. Liqo) installed on top of the clusters.

    Hook execution order (see main.py):

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
        self.spec = spec

    def pre_cluster_init(
        self,
        settings: Settings,
        runtime: Type[BaseRuntime],
        cni: Type[BaseCNI],
    ) -> None:
        # Called once PER CLUSTER, before the runtime creates the cluster.
        # The cluster does not exist yet: no kubeconfig is available.
        pass

    def post_cluster_init(
        self,
        settings: Settings,
        runtime: Type[BaseRuntime],
        cni: Type[BaseCNI],
    ) -> None:
        # Called once PER CLUSTER, right after the cluster is created and its
        # kubeconfig is written, before the CNI is installed.
        pass

    def pre_cni_install(
        self,
        settings: Settings,
        runtime: Type[BaseRuntime],
        cni: Type[BaseCNI],
    ) -> None:
        # Called once PER CLUSTER, right after post_cluster_init and before
        # the CNI installation.
        pass

    def post_cni_install(
        self,
        settings: Settings,
        runtime: Type[BaseRuntime],
        cni: Type[BaseCNI],
    ) -> None:
        # Called once PER CLUSTER, after the CNI is installed (or after the
        # runtime skipped it because the CNI is the runtime default).
        # Last per-cluster hook: the cluster is considered ready here.
        pass

    def after_all_operations(
        self,
        all_settings: Dict[str, Settings],
        all_runtimes: Dict[str, Type[BaseRuntime]],
        all_cnis: Dict[str, Type[BaseCNI]],
    ) -> None:
        # Called ONCE in total, after every cluster went through all the
        # per-cluster hooks. Use it for cross-cluster operations (e.g. peering).
        # Runs BEFORE the namespaces/workloads declared in the config are
        # created.
        # NOTE: `all_settings` actually contains ClusterConfig objects (from
        # core.input), not core.types.Settings: e.g. `.runtime` is a
        # RuntimeConfig, not a string.
        pass
