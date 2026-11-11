from core.input import (
    ClusterConfig,
    DeploymentConfig,
    ManifestConfig,
    NamespaceConfig,
    PodConfig,
    ServiceConfig,
)
from core.log import get_logger
from utils.kubernetes_utils import (
    apply_manifest,
    ensure_kubernetes_namespace,
    create_kubernetes_deployment,
    create_kubernetes_service,
    create_kubernetes_pod,
)


log = get_logger(__name__)


def create_cluster_workloads(kubeconfig: str, config: ClusterConfig):
    """
    Creates the namespaces, then applies the cluster manifests, then the
    namespace manifests and workloads (deployments, services, pods).
    """
    for namespace in config.namespaces:
        ensure_kubernetes_namespace(kubeconfig, namespace.name)

    for manifest in config.manifests:
        apply_manifest_file(kubeconfig, manifest)

    for namespace in config.namespaces:
        log.info(
            "Creating namespace workloads",
            cluster=config.name,
            namespace=namespace.name,
        )
        create_namespace(kubeconfig, namespace)


def apply_manifest_file(
    kubeconfig: str, manifest: ManifestConfig, namespace: str = "default"
):
    log.info("Applying manifest", path=manifest.path, objects=len(manifest.objects))
    apply_manifest(kubeconfig, manifest.objects, default_namespace=namespace)


def create_namespace(kubeconfig: str, config: NamespaceConfig):
    ensure_kubernetes_namespace(kubeconfig, config.name)

    for manifest in config.manifests:
        apply_manifest_file(kubeconfig, manifest, namespace=config.name)

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
