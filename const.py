"""
Constants Module

Global constants used throughout the Kubernetes Testbench application.
"""

# Docker network name for cluster communication
# All k3d clusters are attached to this network to enable direct communication
# between clusters, which is required for multi-cluster features like Liqo peering
DOCKER_NETWORK_NAME = "testbench-net"  # TODO: make configurable
