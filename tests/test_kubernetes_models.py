import unittest

from kubernetes import client
from kubernetes.client import ApiClient

from utils.kubernetes_models import KubernetesValidationError, model_name_for, to_model


class ToModelTest(unittest.TestCase):
    def errors(self, data, type_name: str) -> list[str]:
        with self.assertRaises(KubernetesValidationError) as ctx:
            to_model(data, type_name)
        return ctx.exception.errors

    def test_valid_pod_spec_is_converted_to_client_model(self):
        spec = to_model(
            {
                "containers": [
                    {
                        "name": "c",
                        "image": "nginx",
                        "imagePullPolicy": "IfNotPresent",
                        "ports": [{"containerPort": 80}],
                    }
                ],
                "nodeSelector": {"tier": "worker-0"},
            },
            "V1PodSpec",
        )
        self.assertIsInstance(spec, client.V1PodSpec)
        self.assertIsInstance(spec.containers[0], client.V1Container)
        self.assertEqual(spec.containers[0].image_pull_policy, "IfNotPresent")
        self.assertEqual(spec.containers[0].ports[0].container_port, 80)
        self.assertEqual(spec.node_selector, {"tier": "worker-0"})

    def test_serialization_round_trip(self):
        data = {"ports": [{"port": 80, "targetPort": "http"}], "selector": {"app": "a"}}
        spec = to_model(data, "V1ServiceSpec")
        self.assertEqual(ApiClient().sanitize_for_serialization(spec), data)

    def test_unknown_field_with_hint(self):
        errors = self.errors(
            {"containers": [{"name": "c", "imagePullPolicyy": "Always"}]}, "V1PodSpec"
        )
        self.assertEqual(
            errors,
            [
                "containers.0.imagePullPolicyy: unknown field "
                "(did you mean 'imagePullPolicy'?)"
            ],
        )

    def test_unknown_field_without_hint(self):
        errors = self.errors({"containers": [], "zzzzzz": 1}, "V1PodSpec")
        self.assertEqual(errors, ["zzzzzz: unknown field"])

    def test_type_errors(self):
        errors = self.errors(
            {
                "containers": [
                    {
                        "name": "c",
                        "ports": [{"containerPort": "80"}],
                        "tty": "yes",
                        "args": "not-a-list",
                    }
                ],
                "nodeSelector": {"tier": ["worker-0"]},
            },
            "V1PodSpec",
        )
        self.assertCountEqual(
            errors,
            [
                "containers.0.ports.0.containerPort: expected an integer, got str",
                "containers.0.tty: expected a boolean, got str",
                "containers.0.args: expected a list, got str",
                "nodeSelector.tier: expected a string, got list",
            ],
        )

    def test_bool_is_not_an_integer(self):
        errors = self.errors(
            {"containers": [{"name": "c", "ports": [{"containerPort": True}]}]},
            "V1PodSpec",
        )
        self.assertEqual(
            errors,
            ["containers.0.ports.0.containerPort: expected an integer, got bool"],
        )

    def test_every_missing_required_field_is_reported(self):
        errors = self.errors({"spec": {}}, "V1Deployment")
        self.assertCountEqual(
            errors, ["spec.selector: field required", "spec.template: field required"]
        )

    def test_invalid_field_is_not_also_reported_as_missing(self):
        errors = self.errors(
            {"containers": [{"name": "c", "ports": [{"containerPort": "x"}]}]},
            "V1PodSpec",
        )
        self.assertEqual(
            errors, ["containers.0.ports.0.containerPort: expected an integer, got str"]
        )

    def test_numbers_accepted_as_strings(self):
        spec = to_model(
            {
                "containers": [
                    {"name": "c", "resources": {"limits": {"cpu": 1, "memory": 0.5}}}
                ]
            },
            "V1PodSpec",
        )
        self.assertEqual(
            spec.containers[0].resources.limits, {"cpu": "1", "memory": "0.5"}
        )

    def test_int_or_string_fields_accept_both(self):
        for target_port in (80, "http"):
            spec = to_model(
                {"ports": [{"port": 80, "targetPort": target_port}]}, "V1ServiceSpec"
            )
            self.assertEqual(spec.ports[0].target_port, target_port)

    def test_mapping_expected(self):
        self.assertEqual(
            self.errors("nope", "V1PodSpec"),
            ["expected a mapping (V1PodSpec), got str"],
        )

    def test_null_fields_are_ignored(self):
        spec = to_model(
            {"containers": [{"name": "c"}], "nodeSelector": None}, "V1PodSpec"
        )
        self.assertIsNone(spec.node_selector)


class ModelNameForTest(unittest.TestCase):
    def test_builtin_resources(self):
        self.assertEqual(model_name_for("v1", "Pod"), "V1Pod")
        self.assertEqual(model_name_for("apps/v1", "Deployment"), "V1Deployment")
        self.assertEqual(model_name_for("networking.k8s.io/v1", "Ingress"), "V1Ingress")

    def test_core_event_is_not_the_events_api_one(self):
        self.assertEqual(model_name_for("v1", "Event"), "CoreV1Event")

    def test_custom_resources_are_not_modeled(self):
        self.assertIsNone(model_name_for("monitoring.coreos.com/v1", "ServiceMonitor"))
        # Kind colliding with a built-in one, in a custom group
        self.assertIsNone(model_name_for("example.com/v1", "Deployment"))

    def test_unknown_builtin_kind(self):
        self.assertIsNone(model_name_for("v1", "DoesNotExist"))


if __name__ == "__main__":
    unittest.main()
