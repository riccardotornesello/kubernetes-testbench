import subprocess
from typing import Type, List, Optional, Tuple, Dict

from pydantic import BaseModel, Field

from core.types import Settings
from tools.base import BaseTool
from runtimes.base import BaseRuntime
from cnis.base import BaseCNI
from utils.kubeconfig import get_kubeconfig_location


class LiqoInstallationConfig(BaseModel):
    cluster: str
    version: Optional[str] = None


class LiqoToolSpec(BaseModel):
    installations: List[LiqoInstallationConfig] = Field(default_factory=list)
    peerings: List[Tuple[str, str]] = Field(default_factory=list)


class LiqoTool(BaseTool[LiqoToolSpec]):
    def pre_cluster_init(self) -> None:
        # TODO: check if the clusters are supported
        pass

    def post_cni_install(
        self, settings: Settings, runtime: BaseRuntime, cni: BaseCNI
    ) -> None:
        cluster_installation = next(
            (inst for inst in self.spec.installations if inst.cluster == runtime.name),
            None,
        )
        if cluster_installation is None:
            return

        match settings.runtime:
            case "k3d":
                return self._install_in_cluster(
                    runtime="k3s",
                    cluster_id=settings.name,
                    kubeconfig=get_kubeconfig_location(runtime.name),
                    version=cluster_installation.version,
                    api_server_url=f"https://{runtime.get_api_server_address()}:6443",
                    pod_cidr=settings.cluster_cidr,
                    service_cidr=settings.service_cidr,
                )
            case "kind":
                return self._install_in_cluster(
                    runtime="kind",
                    cluster_id=settings.name,
                    kubeconfig=get_kubeconfig_location(runtime.name),
                    version=cluster_installation.version,
                )
            case _:
                raise ValueError(
                    f"Liqo post CNI install is not supported for runtime: {settings.runtime}"
                )

    def after_all_operations(
        self,
        all_settings: Dict[str, Settings],
        all_runtimes: Dict[str, Type[BaseRuntime]],
        all_cnis: Dict[str, Type[BaseCNI]],
    ) -> None:
        for peering in self.spec.peerings:
            cluster_a = all_settings[peering[0]]
            cluster_b = all_settings[peering[1]]

            # TODO: improve support for different runtimes
            self._peer_clusters(
                kubeconfig=get_kubeconfig_location(cluster_a.name),
                remote_kubeconfig=get_kubeconfig_location(cluster_b.name),
                gw_server_service_type="LoadBalancer"
                if cluster_b.runtime == "k3d"
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


module = LiqoTool
spec = LiqoToolSpec
