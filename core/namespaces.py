from core.input import NamespaceConfig, DeploymentConfig, ServiceConfig, PodConfig
from utils.kubernetes_utils import (
    ensure_kubernetes_namespace,
    create_kubernetes_deployment,
    create_kubernetes_service,
    create_kubernetes_pod,
)


def create_namespace(kubeconfig: str, config: NamespaceConfig):
    ensure_kubernetes_namespace(kubeconfig, config.name)

    for deployment in config.deployments:
        create_deployment(kubeconfig, config.name, deployment)

    for service in config.services:
        create_service(kubeconfig, config.name, service)

    for pod in config.pods:
        create_pod(kubeconfig, config.name, pod)


def create_deployment(kubeconfig: str, namespace: str, deployment: DeploymentConfig):
    return create_kubernetes_deployment(
        kubeconfig_path=kubeconfig,
        deployment_name=deployment.name,
        namespace=namespace,
        replicas=deployment.replicas,
        pod_spec=deployment.pod_spec,
    )


def create_service(kubeconfig: str, namespace: str, service: ServiceConfig):
    return create_kubernetes_service(
        kubeconfig_path=kubeconfig,
        service_name=service.name,
        namespace=namespace,
        service_spec=service.spec,
    )


def create_pod(kubeconfig: str, namespace: str, pod: PodConfig):
    return create_kubernetes_pod(
        kubeconfig_path=kubeconfig,
        pod_name=pod.name,
        namespace=namespace,
        pod_spec=pod.spec,
    )
