import functools
import io
import os
import platform
import tarfile

import docker
import requests

from core.log import get_logger
from core.paths import output_path

log = get_logger(__name__)


@functools.cache
def get_client() -> docker.DockerClient:
    """
    Docker client, created on first use: importing this module does not
    require a running Docker daemon (e.g. to validate a configuration).
    """
    return docker.from_env()


def ensure_docker_container(
    image: str,
    name: str,
    detach: bool = True,
    environment: dict | None = None,
    network: str | None = None,
    volumes: dict | None = None,
    ports: dict | None = None,
    labels: dict | None = None,
) -> docker.models.containers.Container:
    """
    Returns the container `name`, starting it if stopped, or creates it.
    `labels` identify the configuration: an existing container whose labels
    differ is recreated.
    """
    container = get_container(name)
    if container is not None and any(
        container.labels.get(k) != v for k, v in (labels or {}).items()
    ):
        log.info("Container configuration changed, recreating it", container=name)
        container.remove(force=True)
        container = None

    if container is not None:
        # If the container is not running, start it
        if container.status != "running":
            container.start()
            # Refresh the attributes (e.g. the network addresses)
            container.reload()

        return container

    container = get_client().containers.run(
        image=image,
        detach=detach,
        name=name,
        environment=environment,
        network=network,
        volumes=volumes,
        ports=ports,
        labels=labels,
    )
    # The returned attributes predate the start (e.g. no network addresses)
    container.reload()
    return container


def get_container(container_name: str) -> docker.models.containers.Container:
    try:
        return get_client().containers.get(container_name)
    except docker.errors.NotFound:
        return None


def ensure_docker_network(
    network_name: str,
    driver: str = "bridge",
) -> docker.models.networks.Network:
    network = get_network(network_name)
    if network is not None:
        return network

    network = get_client().networks.create(
        name=network_name,
        driver=driver,
        check_duplicate=True,
    )
    return network


def get_network(network_name: str) -> docker.models.networks.Network | None:
    try:
        return get_client().networks.get(network_name)
    except docker.errors.NotFound:
        return None


CNI_PLUGINS_BIN_DIR = "/opt/cni/bin"


def install_cni_plugins(
    container_names: list[str],
    plugins: list[str],
    version: str,
    cache_dir: str | None = None,
) -> None:
    """
    Copies the given reference CNI plugins (containernetworking/plugins) into
    /opt/cni/bin of the given node containers, skipping the plugins already
    present. The release archive is downloaded once and cached locally
    (default: <output dir>/cache).
    """
    if not plugins:
        return
    cache_dir = cache_dir or output_path("cache")

    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine(), platform.machine()
    )
    archive_name = f"cni-plugins-linux-{arch}-v{version}.tgz"
    archive_path = os.path.join(cache_dir, archive_name)

    if not os.path.exists(archive_path):
        os.makedirs(cache_dir, exist_ok=True)
        url = (
            "https://github.com/containernetworking/plugins/releases/download/"
            f"v{version}/{archive_name}"
        )
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        with open(archive_path + ".tmp", "wb") as f:
            f.write(response.content)
        os.replace(archive_path + ".tmp", archive_path)

    # Build an archive containing only the requested plugins
    with tarfile.open(archive_path, "r:gz") as src:
        members = {os.path.basename(m.name): m for m in src.getmembers() if m.isfile()}
        missing = [p for p in plugins if p not in members]
        if missing:
            raise RuntimeError(f"CNI plugins not found in {archive_name}: {missing}")

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as dst:
            for plugin in plugins:
                member = members[plugin]
                info = tarfile.TarInfo(name=plugin)
                info.size = member.size
                info.mode = 0o755
                dst.addfile(info, src.extractfile(member))
        payload = buffer.getvalue()

    for name in container_names:
        container = get_client().containers.get(name)
        existing = container.exec_run(
            ["sh", "-c", f"mkdir -p {CNI_PLUGINS_BIN_DIR} && ls {CNI_PLUGINS_BIN_DIR}"]
        )
        if existing.exit_code != 0:
            raise RuntimeError(
                f"Unable to prepare {CNI_PLUGINS_BIN_DIR} in {name}: {existing.output}"
            )
        present = set(existing.output.decode().split())
        if all(p in present for p in plugins):
            continue

        log.info("Installing CNI plugins", node=name, plugins=",".join(plugins))
        if not container.put_archive(CNI_PLUGINS_BIN_DIR, payload):
            raise RuntimeError(f"Unable to copy CNI plugins into {name}")


def ensure_kernel_modules(container_name: str, modules: list[str]) -> None:
    """
    Loads the given kernel modules from inside a privileged node container
    (kind/k3d nodes share the host kernel).
    """
    container = get_client().containers.get(container_name)
    for module in modules:
        if container.exec_run(["test", "-d", f"/sys/module/{module}"]).exit_code == 0:
            continue

        log.info("Loading kernel module", node=container_name, module=module)
        result = container.exec_run(["modprobe", module])
        if result.exit_code != 0:
            raise RuntimeError(
                f"Kernel module '{module}' is not loaded and cannot be loaded from "
                f"the node containers ({result.output.decode().strip()}). "
                f"Load it on the host: sudo modprobe {module}"
            )
