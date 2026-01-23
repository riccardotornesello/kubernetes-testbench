import subprocess
from typing import Dict

from config import LiqoConfig
from tools.base import Tool
from clusters.k3d import K3d
from clusters.base import Cluster


class LiqoTool(Tool):
    config: LiqoConfig
    clusters: Dict[str, Cluster]

    def __init__(self, config: LiqoConfig, clusters: Dict[str, Cluster]) -> None:
        self.config = config
        self.clusters = clusters

    def install(self) -> None:
        for installation in self.config.installations:
            cluster = self.clusters[installation.cluster]

            if isinstance(cluster, K3d):
                self._install_in_cluster(
                    runtime="k3s",
                    cluster_id=cluster.name,
                    kubeconfig=cluster.get_kubeconfig_location(),
                    version=installation.version,
                    api_server_url=f"https://{cluster.get_api_server_address()}:6443",
                    pod_cidr=cluster.cluster_cidr,
                    service_cidr=cluster.service_cidr,
                )
            else:
                raise ValueError(
                    f"Liqo installation is not supported for cluster: {cluster.name}"
                )

        for peering in self.config.peerings:
            cluster_a = self.clusters[peering[0]]
            cluster_b = self.clusters[peering[1]]

            self._peer_clusters(
                kubeconfig=cluster_a.get_kubeconfig_location(),
                remote_kubeconfig=cluster_b.get_kubeconfig_location(),
                gw_server_service_type="LoadBalancer"
                if isinstance(cluster_b, K3d)
                else "NodePort",
            )

    def _install_in_cluster(
        self,
        runtime: str,
        cluster_id: str,
        kubeconfig: str,
        version: str,
        api_server_url: str | None = None,
        pod_cidr: str | None = None,
        service_cidr: str | None = None,
    ) -> None:
        print(f"Installing Liqo version {version}")

        repo_url = None
        version_hash = None
        if version is not None and version != "latest":
            (repo_url, version_hash) = version.split("@")

        command = [
            "liqoctl",
            "install",
            runtime,
        ]

        # Build installation command by adding parameters
        parameters = {
            "--cluster-id": cluster_id,
            "--pod-cidr": pod_cidr,
            "--service-cidr": service_cidr,
            "--kubeconfig": kubeconfig,
            "--api-server-url": api_server_url,
            "--repo-url": repo_url,
            "--version": version_hash,
        }

        for param, value in parameters.items():
            if value is not None:
                command.extend([param, value])

        print(f"Running command: {' '.join(command)}")

        # Execute installation command
        subprocess.run(command, check=True)

    def _peer_clusters(
        self,
        kubeconfig: str,
        remote_kubeconfig: str,
        gw_server_service_type: str,
    ) -> None:
        print("Peering clusters")

        command = [
            "liqoctl",
            "peer",
        ]

        # Build installation command by adding parameters
        parameters = {
            "--kubeconfig": kubeconfig,
            "--remote-kubeconfig": remote_kubeconfig,
            "--gw-server-service-type": gw_server_service_type,
        }

        for param, value in parameters.items():
            if value is not None:
                command.extend([param, value])

        # Execute peering command
        subprocess.run(command, check=True)
