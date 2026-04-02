import os
import shutil
import subprocess
import yaml

from pydantic import BaseModel

from cnis.base import BaseCNI
from runtimes.base import BaseRuntime
from utils.kubeconfig import get_kubeconfig_location
from utils.ip import get_main_ip


class MinikubeClusterSpec(BaseModel):
    pass


class MinikubeCluster(BaseRuntime[MinikubeClusterSpec]):
    SUPPORTED_CNIS = ["flannel", "cilium", "calico", "calico_bpf"]

    def check_dependencies(self):
        subprocess.run(
            ["minikube", "version"],
            check=True,
        )

    def init_cluster(self) -> None:
        # Create the cluster using minikube CLI

        command = [
            "minikube",
            "start",
            "-p",
            self.name,
            "--nodes",
            str(self.settings.nodes),
            "--cni",
            self.settings.cni,  # TODO: suport calico_bpf
            "--service-cluster-ip-range",
            self.settings.service_cidr,
            "--extra-config",
            "kubeadm.pod-network-cidr=" + self.settings.cluster_cidr,
            "--container-runtime",
            "containerd",
            "--driver",
            "kvm2",
            "--network",
            "testbenchnet",  # TODO: use common network for all runtimes
        ]

        if self.proxy_address is not None:
            real_proxy_address = (
                f"http://{get_main_ip()}:{self.proxy_address.split(':')[-1]}"
            )
            command.extend(["--docker-env", f"HTTP_PROXY={real_proxy_address}"])
            command.extend(["--docker-env", f"HTTPS_PROXY={real_proxy_address}"])
            command.extend(
                [
                    "--docker-env",
                    "NO_PROXY=ttl.sh,storage.googleapis.com,localhost,127.0.0.1,0.0.0.0,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,.local,.svc",
                ]
            )

        print(f"Creating minikube cluster with command: {' '.join(command)}")

        subprocess.run(
            command,
            check=True,
        )

        # Copy kubeconfig
        kubeconfig_location = get_kubeconfig_location(self.name)
        shutil.copyfile(os.path.expanduser("~/.kube/config"), kubeconfig_location)

    def cleanup(self) -> None:
        subprocess.run(
            ["minikube", "delete", "-p", self.name],
            check=True,
        )

    def get_api_server_address(self) -> str:
        kubeconfig_location = get_kubeconfig_location(self.name)
        with open(kubeconfig_location) as f:
            kubeconfig_content = yaml.safe_load(f)
        return kubeconfig_content["clusters"][0]["cluster"]["server"]

    def install_cni(self, cni: BaseCNI) -> None:
        return


module = MinikubeCluster
spec = MinikubeClusterSpec
