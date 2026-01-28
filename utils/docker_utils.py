import docker


client = docker.from_env()


def ensure_docker_container(
    image: str,
    name: str,
    detach: bool = True,
    environment: dict | None = None,
    network: str | None = None,
    volumes: dict | None = None,
) -> docker.models.containers.Container:
    container = get_container(name)
    if container is not None:
        return container

    container = client.containers.run(
        image=image,
        detach=detach,
        name=name,
        environment=environment,
        network=network,
        volumes=volumes,
    )
    return container


def get_container(container_name: str) -> docker.models.containers.Container:
    try:
        return client.containers.get(container_name)
    except docker.errors.NotFound:
        return None


def ensure_docker_network(
    network_name: str,
    driver: str = "bridge",
) -> docker.models.networks.Network:
    network = get_network(network_name)
    if network is not None:
        return network

    network = client.networks.create(
        name=network_name,
        driver=driver,
        check_duplicate=True,
    )
    return network


def get_network(network_name: str) -> docker.models.networks.Network | None:
    try:
        return client.networks.get(network_name)
    except docker.errors.NotFound:
        return None
