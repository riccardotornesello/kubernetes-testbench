import unittest
from unittest import mock

from kubernetes import client

from core import workloads
from tests.helpers import ValidationTestCase, cluster
from utils import kubernetes_utils

POD_SPEC = {"containers": [{"name": "c", "image": "nginx"}]}


class CreateClusterWorkloadsTest(ValidationTestCase):
    def test_order(self):
        self.write(
            "cluster.yaml", "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: c}\n"
        )
        self.write("ns.yaml", "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: n}\n")
        cfg = self.assert_valid(
            {
                "clusters": [
                    cluster(
                        manifests=["cluster.yaml"],
                        namespaces=[
                            {
                                "name": "ns1",
                                "manifests": ["ns.yaml"],
                                "deployments": [{"name": "d", "pod_spec": POD_SPEC}],
                                "services": [{"name": "s"}],
                                "pods": [{"name": "p", "spec": POD_SPEC}],
                            },
                            {"name": "ns2"},
                        ],
                    )
                ]
            }
        )

        calls = []

        def recorder(name):
            def record(*args, **kwargs):
                calls.append((name, args, kwargs))

            return record

        with mock.patch.multiple(
            workloads,
            ensure_kubernetes_namespace=recorder("namespace"),
            apply_manifest=recorder("manifest"),
            create_kubernetes_deployment=recorder("deployment"),
            create_kubernetes_service=recorder("service"),
            create_kubernetes_pod=recorder("pod"),
        ):
            workloads.create_cluster_workloads("kubeconfig", cfg.clusters[0])

        def describe(call):
            name, args, kwargs = call
            if name == "namespace":
                return f"namespace {args[1]}"
            if name == "manifest":
                obj = args[1][0]["metadata"]["name"]
                return f"manifest {obj} in {kwargs['default_namespace']}"
            return f"{name} {kwargs['namespace']}"

        self.assertEqual(
            [describe(c) for c in calls],
            [
                # Every namespace first: cluster manifests may refer to them
                "namespace ns1",
                "namespace ns2",
                "manifest c in default",
                "namespace ns1",
                "manifest n in ns1",
                "deployment ns1",
                "service ns1",
                "pod ns1",
                "namespace ns2",
            ],
        )
        deployment_kwargs = calls[5][2]
        self.assertEqual(deployment_kwargs["deployment_name"], "d")
        self.assertIsInstance(deployment_kwargs["pod_spec"], client.V1PodSpec)


@mock.patch("utils.kubernetes_utils.config.new_client_from_config")
class KubernetesObjectsTest(unittest.TestCase):
    def test_deployment(self, _):
        pod_spec = client.V1PodSpec(containers=[client.V1Container(name="c")])
        with mock.patch("utils.kubernetes_utils.client.AppsV1Api") as api:
            kubernetes_utils.create_kubernetes_deployment(
                "kc", "web", "ns", 2, pod_spec
            )
        kwargs = api.return_value.create_namespaced_deployment.call_args.kwargs
        body = kwargs["body"]
        self.assertEqual(kwargs["namespace"], "ns")
        self.assertEqual(body.spec.replicas, 2)
        self.assertEqual(body.spec.selector.match_labels, {"app": "web"})
        self.assertEqual(body.spec.template.metadata.labels, {"app": "web"})
        self.assertIs(body.spec.template.spec, pod_spec)

    def test_pod_and_service(self, _):
        with mock.patch("utils.kubernetes_utils.client.CoreV1Api") as api:
            kubernetes_utils.create_kubernetes_pod(
                "kc", "p", "ns", client.V1PodSpec(containers=[])
            )
            kubernetes_utils.create_kubernetes_service(
                "kc", "s", "ns", client.V1ServiceSpec()
            )
        pod = api.return_value.create_namespaced_pod.call_args.kwargs["body"]
        self.assertEqual((pod.metadata.name, pod.metadata.labels), ("p", {"app": "p"}))
        service = api.return_value.create_namespaced_service.call_args.kwargs["body"]
        self.assertEqual(service.metadata.name, "s")


@mock.patch("utils.kubernetes_utils.config.new_client_from_config")
class ApplyManifestTest(unittest.TestCase):
    def apply(self, objects, namespaced: bool, **kwargs) -> mock.Mock:
        with mock.patch("utils.kubernetes_utils.DynamicClient") as dyn:
            resource = dyn.return_value.resources.get.return_value
            resource.namespaced = namespaced
            kubernetes_utils.apply_manifest("kc", objects, **kwargs)
        return resource

    def obj(self, **metadata) -> dict:
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "x", **metadata},
        }

    def test_default_namespace(self, _):
        resource = self.apply([self.obj()], namespaced=True, default_namespace="ns")
        self.assertEqual(resource.server_side_apply.call_args.kwargs["namespace"], "ns")

    def test_object_namespace_wins(self, _):
        resource = self.apply(
            [self.obj(namespace="other")], namespaced=True, default_namespace="ns"
        )
        self.assertEqual(
            resource.server_side_apply.call_args.kwargs["namespace"], "other"
        )

    def test_cluster_scoped(self, _):
        resource = self.apply([self.obj()], namespaced=False, default_namespace="ns")
        kwargs = resource.server_side_apply.call_args.kwargs
        self.assertIsNone(kwargs["namespace"])
        self.assertEqual(kwargs["field_manager"], "testbench")


if __name__ == "__main__":
    unittest.main()
