import os

from utils.docker_utils import ensure_docker_container
from const import DOCKER_NETWORK_NAME


REGISTRY_PROXY_IMAGE = "rpardini/docker-registry-proxy:0.6.5"  # TODO: variable
REGISTRY_PROXY_CONTAINER_NAME = "testbench-registry-proxy"
REGISTRY_PROXY_CA_VOLUME = os.path.abspath("./out/registry-proxy/ca")
REGISTRY_PROXY_CACHE_VOLUME = os.path.abspath("./out/registry-proxy/cache")


def run_registry_proxy_container() -> str:
    # Start the registry proxy container
    container = ensure_docker_container(
        image=REGISTRY_PROXY_IMAGE,
        name=REGISTRY_PROXY_CONTAINER_NAME,
        environment={
            "ENABLE_MANIFEST_CACHE": "true",
        },
        network=DOCKER_NETWORK_NAME,
        volumes={
            REGISTRY_PROXY_CA_VOLUME: {"bind": "/ca", "mode": "rw"},
            REGISTRY_PROXY_CACHE_VOLUME: {"bind": "/docker_mirror_cache", "mode": "rw"},
        },
    )

    # Fetch container IP
    network_settings = container.attrs["NetworkSettings"]["Networks"]
    if DOCKER_NETWORK_NAME not in network_settings:
        raise RuntimeError(
            f"Proxy container not connected to network: {DOCKER_NETWORK_NAME}"
        )

    return f"http://{network_settings[DOCKER_NETWORK_NAME]['IPAddress']}:3128"
