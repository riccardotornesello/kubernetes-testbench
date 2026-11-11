"""Constants shared by the runtimes and the tools."""

# Docker network shared by the clusters and the registry proxy. kind always
# uses this one, so k3d and minikube (docker driver) join it too.
DOCKER_NETWORK_NAME = "kind"
