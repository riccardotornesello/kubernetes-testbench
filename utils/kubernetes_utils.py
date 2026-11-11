import time

import requests
import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.exceptions import ResourceNotFoundError


def ensure_kubernetes_namespace(kubeconfig: str, namespace_name: str) -> bool:
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


def create_kubernetes_deployment(
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


def create_kubernetes_service(
    kubeconfig_path: str,
    service_name: str,
    namespace: str,
    service_spec: dict,
):
    k8s_client = config.new_client_from_config(config_file=kubeconfig_path)
    core_v1 = client.CoreV1Api(api_client=k8s_client)

    service = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(name=service_name),
        spec=service_spec,
    )

    core_v1.create_namespaced_service(namespace=namespace, body=service)


def create_kubernetes_pod(
    kubeconfig_path: str,
    pod_name: str,
    namespace: str,
    pod_spec: dict,
):
    k8s_client = config.new_client_from_config(config_file=kubeconfig_path)
    core_v1 = client.CoreV1Api(api_client=k8s_client)

    labels = {"app": pod_name}

    pod = client.V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=client.V1ObjectMeta(name=pod_name, labels=labels),
        spec=pod_spec,
    )

    core_v1.create_namespaced_pod(namespace=namespace, body=pod)


# ---------------------------------------------------------------------------
# Manifest helpers (used by the CNI installers)
# ---------------------------------------------------------------------------

FIELD_MANAGER = "testbench"


def fetch_manifest(url: str) -> list[dict]:
    """Downloads a (multi-document) YAML manifest and returns its objects."""
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return [doc for doc in yaml.safe_load_all(response.text) if doc]


def apply_manifest(
    kubeconfig_path: str,
    objects: list[dict],
    timeout: int = 120,
) -> None:
    """
    Server-side applies a list of objects, in order.

    Resource types not yet known to the API server (e.g. custom resources
    whose CRD was applied a moment ago) are retried until `timeout`.
    """
    k8s_client = config.new_client_from_config(config_file=kubeconfig_path)
    dyn_client = DynamicClient(k8s_client)

    for obj in objects:
        deadline = time.monotonic() + timeout
        while True:
            try:
                resource = dyn_client.resources.get(
                    api_version=obj["apiVersion"], kind=obj["kind"]
                )
                break
            except ResourceNotFoundError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)
                dyn_client.resources.invalidate_cache()

        namespace = None
        if resource.namespaced:
            namespace = obj.get("metadata", {}).get("namespace", "default")

        resource.server_side_apply(
            body=obj,
            name=obj["metadata"]["name"],
            namespace=namespace,
            field_manager=FIELD_MANAGER,
            force_conflicts=True,
        )


def get_api_server_endpoint(kubeconfig_path: str) -> tuple[str, int]:
    """
    Returns the (host, port) at which the API server is reachable from inside
    the cluster nodes, read from the `default/kubernetes` endpoints.
    """
    k8s_client = config.new_client_from_config(config_file=kubeconfig_path)
    core_v1 = client.CoreV1Api(api_client=k8s_client)

    endpoints = core_v1.read_namespaced_endpoints("kubernetes", "default")
    for subset in endpoints.subsets or []:
        port = next((p.port for p in subset.ports or [] if p.name == "https"), None)
        for address in subset.addresses or []:
            if port is not None:
                return address.ip, port

    raise RuntimeError("API server endpoint not found")


def wait_for_nodes_ready(kubeconfig_path: str, timeout: int) -> None:
    k8s_client = config.new_client_from_config(config_file=kubeconfig_path)
    core_v1 = client.CoreV1Api(api_client=k8s_client)

    def not_ready_nodes() -> list[str]:
        result = []
        for node in core_v1.list_node().items:
            conditions = node.status.conditions or []
            if not any(c.type == "Ready" and c.status == "True" for c in conditions):
                result.append(node.metadata.name)
        return result

    _wait_until(
        lambda: not not_ready_nodes(),
        timeout,
        lambda: f"nodes not ready: {not_ready_nodes()}",
    )


def wait_for_workloads_ready(
    kubeconfig_path: str,
    namespace: str,
    timeout: int,
    names: list[str] | None = None,
) -> None:
    """
    Waits until every DaemonSet and Deployment in `namespace` (or only the
    ones in `names`, if given) is fully rolled out. At least one workload
    must exist: namespaces populated asynchronously by operators (e.g.
    calico-system) are waited for as well.
    """
    k8s_client = config.new_client_from_config(config_file=kubeconfig_path)
    apps_v1 = client.AppsV1Api(api_client=k8s_client)

    def pending_workloads() -> list[str] | None:
        workloads = []
        for ds in apps_v1.list_namespaced_daemon_set(namespace).items:
            s = ds.status
            ready = (s.desired_number_scheduled or 0) == (s.number_ready or 0) and (
                s.updated_number_scheduled or 0
            ) == (s.desired_number_scheduled or 0)
            workloads.append((f"daemonset/{ds.metadata.name}", ready))
        for dep in apps_v1.list_namespaced_deployment(namespace).items:
            ready = (dep.status.available_replicas or 0) >= (dep.spec.replicas or 0)
            workloads.append((f"deployment/{dep.metadata.name}", ready))

        if names is not None:
            workloads = [w for w in workloads if w[0].split("/", 1)[1] in names]
            if len(workloads) < len(names):
                return None
        elif not workloads:
            return None

        return [name for name, ready in workloads if not ready]

    def describe() -> str:
        pending = pending_workloads()
        if pending is None:
            return f"workloads not created yet in namespace '{namespace}'"
        return f"workloads not ready in namespace '{namespace}': {pending}"

    _wait_until(lambda: pending_workloads() == [], timeout, describe)


def _wait_until(condition, timeout: int, describe, interval: int = 5) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if condition():
                return
        except ApiException as e:
            if e.status != 404:
                raise
        if time.monotonic() > deadline:
            raise TimeoutError(f"Timeout after {timeout}s: {describe()}")
        time.sleep(interval)


def label_nodes(kubeconfig_path: str, labels: dict[str, dict[str, str]]) -> None:
    """Sets the given labels, as {node name: {key: value}}, on the nodes."""
    k8s_client = config.new_client_from_config(config_file=kubeconfig_path)
    core_v1 = client.CoreV1Api(api_client=k8s_client)
    for node, node_labels in labels.items():
        core_v1.patch_node(node, {"metadata": {"labels": node_labels}})
