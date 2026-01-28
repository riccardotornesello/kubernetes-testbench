from kubernetes import client, config
from kubernetes.client.rest import ApiException


def create_kubernetes_namespace(kubeconfig: str, namespace_name: str) -> bool:
    k8s_client = config.new_client_from_config(config_file=kubeconfig)
    core_v1 = client.CoreV1Api(api_client=k8s_client)

    namespace_manifest = client.V1Namespace(
        metadata=client.V1ObjectMeta(name=namespace_name)
    )

    try:
        core_v1.create_namespace(body=namespace_manifest)
        return True

    except ApiException as e:
        if e.status == 409:
            return False  # Namespace already exists
        else:
            raise  # Rethrow other exceptions


def create_deployment(
    kubeconfig_path: str,
    deployment_name: str,
    namespace: str,
    replicas: int,
    pod_spec: dict,
):
    k8s_client = config.new_client_from_config(config_file=kubeconfig_path)
    apps_v1 = client.AppsV1Api(api_client=k8s_client)

    labels = {"app": deployment_name}

    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels=labels),
        spec=pod_spec,
    )

    spec = client.V1DeploymentSpec(
        replicas=replicas,
        template=template,
        selector=client.V1LabelSelector(match_labels=labels),
    )

    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(name=deployment_name),
        spec=spec,
    )

    apps_v1.create_namespaced_deployment(namespace=namespace, body=deployment)
