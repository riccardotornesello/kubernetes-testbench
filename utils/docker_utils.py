"""Docker helpers: containers, networks and node containers."""

import functools
import hashlib
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


def get_container(container_name: str) -> docker.models.containers.Container | None:
    """Container `container_name`, or None."""
    try:
        return get_client().containers.get(container_name)
    except docker.errors.NotFound:
        return None


# Bridge option of Docker >= 28: NAT towards the outside, but no rule
# dropping the packets routed to the containers from other interfaces (the
# testbench networks reach each other through the host routing)
GATEWAY_MODE_OPTION = "com.docker.network.bridge.gateway_mode_ipv4"
GATEWAY_MODE = "nat-unprotected"


def server_version() -> tuple[int, ...]:
    """Version of the Docker daemon, e.g. (29, 1, 3)."""
    version = get_client().version()["Version"]
    return tuple(
        int(part) for part in version.split("-")[0].split(".") if part.isdigit()
    )


def firewall_backend() -> str:
    """
    Firewall backend of the Docker daemon: `iptables` (possibly
    `iptables+firewalld`) or `nftables`. Daemons older than 29 do not report
    it and only support iptables.
    """
    info = get_client().info()
    return ((info.get("FirewallBackend") or {}).get("Driver")) or "iptables"


def ensure_docker_network(
    network_name: str,
    subnet: str,
    gateway: str,
) -> docker.models.networks.Network:
    """
    Bridge network `network_name` with the given subnet and gateway (the
    host address on it), created if missing. An existing network with
    another configuration is recreated: the containers attached to it are
    disconnected first.
    """
    options = {GATEWAY_MODE_OPTION: GATEWAY_MODE} if server_version() >= (28,) else {}

    network = get_network(network_name)
    if network is not None:
        config = network.attrs.get("IPAM", {}).get("Config") or [{}]
        current_options = network.attrs.get("Options") or {}
        if (
            network.attrs.get("Driver") == "bridge"
            and config[0].get("Subnet") == subnet
            and config[0].get("Gateway") == gateway
            and all(current_options.get(k) == v for k, v in options.items())
        ):
            return network

        log.info("Network configuration changed, recreating it", network=network_name)
        network.reload()
        for container in network.containers:
            log.info(
                "Disconnecting container",
                network=network_name,
                container=container.name,
            )
            network.disconnect(container, force=True)
        network.remove()

    ipam = docker.types.IPAMConfig(
        pool_configs=[docker.types.IPAMPool(subnet=subnet, gateway=gateway)]
    )
    return get_client().networks.create(
        name=network_name,
        driver="bridge",
        ipam=ipam,
        options=options,
        check_duplicate=True,
    )


def ensure_container_connected(
    container: docker.models.containers.Container, network_name: str
) -> docker.models.containers.Container:
    """Connects `container` to `network_name` if needed, returning it refreshed."""
    if network_name not in container.attrs["NetworkSettings"]["Networks"]:
        get_client().networks.get(network_name).connect(container)
        container.reload()
    return container


def get_network(network_name: str) -> docker.models.networks.Network | None:
    """Network `network_name`, or None."""
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
        # Checked against the checksum published with the release: the
        # archive is cached and its binaries run on the nodes
        checksum = requests.get(url + ".sha256", timeout=30)
        checksum.raise_for_status()
        expected = checksum.text.split()[0]
        actual = hashlib.sha256(response.content).hexdigest()
        if actual != expected:
            raise RuntimeError(
                f"Checksum mismatch for {archive_name}: {actual} != {expected}"
            )
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
