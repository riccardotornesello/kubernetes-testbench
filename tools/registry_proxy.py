"""Registry proxy tool: caching proxy of the images pulled by the nodes."""

import hashlib
import json
import os
import re
import time
from typing import List, Optional

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.log import get_logger
from core.paths import output_path
from core.settings import INFRA_NETWORK_NAME
from core.types import Settings
from runtimes.base import NodeProxy
from tools.base import (
    AnyCNI,
    AnyRuntime,
    BaseTool,
    BaseToolSpec,
    check_known_clusters,
)
from utils.docker_utils import ensure_container_connected, ensure_docker_container

log = get_logger(__name__)


CONTAINER_NAME = "testbench-registry-proxy"
CONTAINER_PORT = 3128
CONFIG_LABEL = "testbench.registry-proxy/config-hash"
# Data of the proxy, persisted across runs in the output directory
CA_SUBDIR = ("registry-proxy", "ca")
CACHE_SUBDIR = ("registry-proxy", "cache")

# Destinations the nodes reach directly, without the proxy (the runtime adds
# the cluster pod/service CIDRs)
DEFAULT_NO_PROXY = [
    "ttl.sh",
    "storage.googleapis.com",
    "localhost",
    "127.0.0.0/8",
    "0.0.0.0",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    ".local",
    ".svc",
]


# Host, domain suffix, IP or CIDR, optionally with a port
_HOST_RE = re.compile(r"^[A-Za-z0-9.*:/_-]+$")


class RegistryAuth(BaseModel):
    """Credentials for an upstream registry."""

    model_config = ConfigDict(extra="forbid")

    # Registry host (Docker Hub: auth.docker.io)
    registry: str
    username: str
    # Name of the environment variable holding the password/token: secrets
    # are not written in the configuration
    password_env: str


class RegistryProxyToolSpec(BaseToolSpec):
    # Clusters whose nodes pull through the proxy (default: all)
    clusters: Optional[List[str]] = None
    image: str = "rpardini/docker-registry-proxy:0.6.5"
    # Registries cached besides Docker Hub (always cached). The others are
    # reached through the proxy without caching.
    registries: List[str] = Field(
        default_factory=lambda: ["registry.k8s.io", "quay.io", "ghcr.io", "gcr.io"]
    )
    # Cache the manifests too (tags are not re-resolved upstream)
    manifest_cache: bool = True
    # Credentials used by the proxy for the upstream registries (e.g. to
    # avoid the Docker Hub pull rate limit)
    auth: List[RegistryAuth] = Field(default_factory=list)
    # Destinations the nodes reach without the proxy, besides the defaults
    extra_no_proxy: List[str] = Field(default_factory=list)
    # Seconds to wait for the proxy to be ready
    timeout: int = Field(default=120, gt=0)

    @field_validator("registries", "extra_no_proxy")
    @classmethod
    def _validate_hosts(cls, values: list[str]) -> list[str]:
        # Written into shell scripts and space/comma separated variables
        for value in values:
            if not _HOST_RE.match(value):
                raise ValueError(f"invalid host '{value}'")
        return values

    def validate_clusters(self, clusters: list[str]) -> None:
        if self.clusters is not None:
            check_known_clusters(self.clusters, clusters)


class RegistryProxyTool(BaseTool[RegistryProxyToolSpec]):
    """
    Runs a caching registry proxy (rpardini/docker-registry-proxy) shared by
    every cluster, and makes the nodes of the selected clusters pull their
    images through it. The cache (and the proxy CA) persist across runs in
    <output dir>/registry-proxy.
    """

    def __init__(self, spec: RegistryProxyToolSpec) -> None:
        super().__init__(spec)
        self.proxy: NodeProxy | None = None

    def _selected(self, cluster: str) -> bool:
        """Whether the nodes of `cluster` pull through the proxy."""
        return self.spec.clusters is None or cluster in self.spec.clusters

    @property
    def uses_infra_network(self) -> bool:
        return self.spec.clusters != []

    def check_dependencies(self) -> None:
        missing = [
            a.password_env for a in self.spec.auth if a.password_env not in os.environ
        ]
        if missing:
            raise RuntimeError(
                f"registry_proxy: environment variables not set: {missing}"
            )

    def before_all_operations(
        self,
        all_settings: dict[str, Settings],
        all_runtimes: dict[str, AnyRuntime],
        all_cnis: dict[str, AnyCNI],
    ) -> None:
        if not any(self._selected(name) for name in all_runtimes):
            return
        self.proxy = self._start()

    def pre_cluster_init(
        self, settings: Settings, runtime: AnyRuntime, cni: AnyCNI
    ) -> None:
        if self.proxy is not None and self._selected(runtime.name):
            runtime.set_node_proxy(self.proxy)

    def _environment(self) -> dict[str, str]:
        """Environment variables of the proxy container."""
        env = {
            "ENABLE_MANIFEST_CACHE": str(self.spec.manifest_cache).lower(),
            "REGISTRIES": " ".join(self.spec.registries),
        }
        if self.spec.auth:
            env["AUTH_REGISTRIES"] = " ".join(
                f"{a.registry}:{a.username}:{os.environ[a.password_env]}"
                for a in self.spec.auth
            )
        return env

    def _start(self) -> NodeProxy:
        ca_dir, cache_dir = output_path(*CA_SUBDIR), output_path(*CACHE_SUBDIR)

        environment = self._environment()
        volumes = {
            ca_dir: {"bind": "/ca", "mode": "rw"},
            cache_dir: {"bind": "/docker_mirror_cache", "mode": "rw"},
        }
        # The volumes are part of the configuration: a container of another
        # output directory is recreated (it would serve another CA)
        config_hash = hashlib.sha256(
            json.dumps(
                {
                    "image": self.spec.image,
                    "env": environment,
                    "volumes": volumes,
                    "network": INFRA_NETWORK_NAME,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:16]

        log.info(
            "Starting registry proxy", container=CONTAINER_NAME, image=self.spec.image
        )
        container = ensure_docker_container(
            image=self.spec.image,
            name=CONTAINER_NAME,
            environment=environment,
            network=INFRA_NETWORK_NAME,
            volumes=volumes,
            labels={CONFIG_LABEL: config_hash},
        )
        # Disconnected when the infrastructure network is recreated (e.g. new
        # address pool)
        container = ensure_container_connected(container, INFRA_NETWORK_NAME)

        networks = container.attrs["NetworkSettings"]["Networks"]
        ip = networks.get(INFRA_NETWORK_NAME, {}).get("IPAddress")
        if not ip:
            raise RuntimeError(
                f"Registry proxy not connected to network {INFRA_NETWORK_NAME}"
            )

        proxy = NodeProxy(
            address=f"http://{ip}:{CONTAINER_PORT}",
            ca_file=os.path.join(ca_dir, "ca.crt"),
            no_proxy=[*DEFAULT_NO_PROXY, *self.spec.extra_no_proxy],
        )
        self._wait_ready(proxy)
        return proxy

    def _wait_ready(self, proxy: NodeProxy) -> None:
        # The CA is generated at the first start: it must exist before the
        # nodes are created (k3d mounts it)
        session = requests.Session()
        session.trust_env = False  # never through the host proxy settings
        deadline = time.monotonic() + self.spec.timeout
        while True:
            try:
                response = session.get(proxy.address + proxy.ca_url_path, timeout=5)
                if response.ok and os.path.isfile(proxy.ca_file):
                    return
            except requests.RequestException:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"Registry proxy not ready after {self.spec.timeout}s"
                )
            time.sleep(2)


module = RegistryProxyTool
spec = RegistryProxyToolSpec
