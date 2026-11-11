import glob
import os
import unittest

from kubernetes import client

from core.input import validate_config_file
from tests.helpers import REPO_ROOT, ValidationTestCase, cluster

POD_SPEC = {"containers": [{"name": "c", "image": "nginx"}]}

MANIFEST = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: cfg
data:
  key: value
---
apiVersion: example.com/v1
kind: Widget
metadata:
  name: w
spec:
  anything: true
"""


class ClusterValidationTest(ValidationTestCase):
    def test_defaults(self):
        cfg = self.assert_valid({"clusters": [cluster()]})
        c = cfg.clusters[0]
        self.assertEqual(c.cni.type, "flannel")
        self.assertEqual(c.nodes, 1)
        self.assertEqual(c.cluster_cidr, "10.244.0.0/16")
        self.assertEqual(c.service_cidr, "10.96.0.0/16")
        self.assertEqual(cfg.tools, [])

    def test_type_shorthand(self):
        cfg = self.assert_valid({"clusters": [cluster(cni="calico")]})
        self.assertEqual(cfg.clusters[0].cni.type, "calico")
        self.assertEqual(cfg.clusters[0].cni.spec.dataplane, "Iptables")

    def test_unknown_runtime(self):
        errors = self.validation_errors({"clusters": [cluster(runtime="nope")]})
        self.assert_error(errors, "clusters.0.runtime")

    def test_unknown_spec_field(self):
        errors = self.validation_errors(
            {"clusters": [cluster(runtime={"type": "kind", "spec": {"foo": 1}})]}
        )
        self.assert_error(errors, "clusters.0.runtime.kind.spec.foo", "Extra inputs")

    def test_unknown_cluster_field(self):
        errors = self.validation_errors({"clusters": [cluster(foo=1)]})
        self.assert_error(errors, "clusters.0.foo", "Extra inputs")

    def test_duplicate_cluster_names(self):
        errors = self.validation_errors({"clusters": [cluster("a"), cluster("a")]})
        self.assert_error(errors, "Duplicate cluster name", "'a'")

    def test_invalid_cidr(self):
        errors = self.validation_errors(
            {"clusters": [cluster(cluster_cidr="10.0.0.1/16")]}
        )
        self.assert_error(errors, "clusters.0.cluster_cidr", "invalid IPv4 CIDR")

    def test_overlapping_cidrs(self):
        errors = self.validation_errors(
            {
                "clusters": [
                    cluster(cluster_cidr="10.0.0.0/16", service_cidr="10.0.128.0/20")
                ]
            }
        )
        self.assert_error(errors, "overlaps service_cidr")

    def test_cluster_cidr_too_small_for_nodes(self):
        errors = self.validation_errors(
            {"clusters": [cluster(cluster_cidr="10.0.0.0/23", nodes=3)]}
        )
        self.assert_error(errors, "does not contain a /24", "3 nodes")

    def test_cni_specific_validation(self):
        errors = self.validation_errors(
            {
                "clusters": [
                    cluster(
                        cluster_cidr="10.0.0.0/24",
                        cni={"type": "calico", "spec": {"block_size": 20}},
                    )
                ]
            }
        )
        self.assert_error(errors, "clusters.0", "block_size /20 is larger")

    def test_empty_and_non_mapping_configuration(self):
        for data in (None, ["a"]):
            with self.assertLogs("core.input", "ERROR"):
                from core.input import validate_data

                self.assertIsNone(validate_data(data))


class DefaultTest(ValidationTestCase):
    def test_default_is_merged_into_clusters(self):
        cfg = self.assert_valid(
            {
                "default": {"runtime": "k3d", "nodes": 2},
                "clusters": [{"name": "a"}, {"name": "b", "nodes": 3}],
            }
        )
        self.assertEqual(
            [(c.name, c.runtime.type, c.nodes) for c in cfg.clusters],
            [("a", "k3d", 2), ("b", "k3d", 3)],
        )

    def test_merge_is_shallow(self):
        cfg = self.assert_valid(
            {
                "default": {
                    "runtime": "kind",
                    "cni": {"type": "calico", "spec": {"mtu": 1400}},
                },
                "clusters": [{"name": "a", "cni": {"type": "calico"}}],
            }
        )
        self.assertIsNone(cfg.clusters[0].cni.spec.mtu)

    def test_input_is_not_modified(self):
        data = {"default": {"runtime": "kind"}, "clusters": [{"name": "a"}]}
        self.assert_valid(data)
        self.assertEqual(data["clusters"], [{"name": "a"}])

    def test_default_errors_are_reported_once(self):
        errors = self.validation_errors(
            {
                "default": {
                    "runtime": "kind",
                    "nodes": 0,
                    "namespaces": [{"name": "ns", "pods": [{"name": "p", "spec": {}}]}],
                },
                "clusters": [{"name": "a"}, {"name": "b"}, {"name": "c", "nodes": 0}],
            }
        )
        self.assertEqual(
            sorted(errors),
            sorted(
                [
                    "default.nodes: Input should be greater than or equal to 1",
                    "default.namespaces.0.pods.0.spec: containers: field required",
                    # Set by the cluster itself, not inherited
                    "clusters.2.nodes: Input should be greater than or equal to 1",
                ]
            ),
        )

    def test_unknown_field_and_name_in_default(self):
        errors = self.validation_errors(
            {
                "default": {"runtime": "kind", "foo": 1, "name": "x"},
                "clusters": [{"name": "a"}],
            }
        )
        self.assert_error(errors, "default.foo", "Extra inputs")
        self.assert_error(errors, "default.name", "not allowed")
        self.assertFalse([e for e in errors if e.startswith("clusters.")])

    def test_fields_missing_from_default_are_reported_on_the_clusters(self):
        errors = self.validation_errors(
            {"default": {"nodes": 2}, "clusters": [{"name": "a"}]}
        )
        self.assertEqual(errors, ["clusters.0.runtime: Field required"])

    def test_cross_field_errors_are_reported_per_cluster(self):
        errors = self.validation_errors(
            {
                "default": {
                    "runtime": "kind",
                    "cni": {"type": "calico", "spec": {"block_size": 20}},
                },
                "clusters": [
                    {"name": "a", "cluster_cidr": "10.0.0.0/24"},
                    {"name": "b"},
                ],
            }
        )
        self.assertEqual(len(errors), 1)
        self.assert_error(errors, "clusters.0", "block_size")


class WorkloadValidationTest(ValidationTestCase):
    def namespace(self, **fields) -> dict:
        return {"clusters": [cluster(namespaces=[{"name": "ns", **fields}])]}

    def test_workloads_are_converted_to_client_models(self):
        cfg = self.assert_valid(
            self.namespace(
                deployments=[{"name": "d", "replicas": 2, "pod_spec": POD_SPEC}],
                services=[{"name": "s", "spec": {"ports": [{"port": 80}]}}],
                pods=[{"name": "p", "spec": POD_SPEC}],
            )
        )
        ns = cfg.clusters[0].namespaces[0]
        self.assertIsInstance(ns.deployments[0].pod_spec, client.V1PodSpec)
        self.assertIsInstance(ns.services[0].spec, client.V1ServiceSpec)
        self.assertIsInstance(ns.pods[0].spec, client.V1PodSpec)

    def test_service_spec_is_optional(self):
        cfg = self.assert_valid(self.namespace(services=[{"name": "s"}]))
        self.assertIsInstance(
            cfg.clusters[0].namespaces[0].services[0].spec, client.V1ServiceSpec
        )

    def test_pod_spec_is_required(self):
        errors = self.validation_errors(self.namespace(pods=[{"name": "p"}]))
        self.assert_error(errors, "pods.0.spec", "Field required")

    def test_spec_errors_have_their_path(self):
        errors = self.validation_errors(
            self.namespace(
                pods=[{"name": "p", "spec": {"containers": [{"image": "x"}]}}]
            )
        )
        self.assertEqual(
            errors,
            ["clusters.0.namespaces.0.pods.0.spec: containers.0.name: field required"],
        )

    def test_invalid_names(self):
        errors = self.validation_errors(
            {
                "clusters": [
                    cluster(
                        namespaces=[
                            {
                                "name": "Bad_NS",
                                "services": [{"name": "1svc"}],
                                "pods": [{"name": "-p", "spec": POD_SPEC}],
                                "deployments": [
                                    {"name": "a" * 254, "pod_spec": POD_SPEC}
                                ],
                            }
                        ]
                    )
                ]
            }
        )
        self.assert_error(errors, "namespaces.0.name", "RFC 1123 label")
        self.assert_error(errors, "services.0.name", "DNS-1035 label")
        self.assert_error(errors, "pods.0.name", "RFC 1123 subdomain")
        self.assert_error(errors, "deployments.0.name", "RFC 1123 subdomain")

    def test_valid_names(self):
        self.assert_valid(
            self.namespace(
                pods=[{"name": "my.pod-1", "spec": POD_SPEC}],
                services=[{"name": "svc-1"}],
            )
        )

    def test_duplicate_names(self):
        errors = self.validation_errors(
            self.namespace(pods=[{"name": "p", "spec": POD_SPEC}] * 2)
        )
        self.assert_error(errors, "duplicate pod name 'p'")

        errors = self.validation_errors(
            {"clusters": [cluster(namespaces=[{"name": "ns"}, {"name": "ns"}])]}
        )
        self.assert_error(errors, "duplicate namespace name 'ns'")

    def test_same_name_for_different_kinds(self):
        self.assert_valid(
            self.namespace(
                deployments=[{"name": "web", "pod_spec": POD_SPEC}],
                services=[{"name": "web"}],
            )
        )

    def test_negative_replicas(self):
        errors = self.validation_errors(
            self.namespace(
                deployments=[{"name": "d", "replicas": -1, "pod_spec": POD_SPEC}]
            )
        )
        self.assert_error(errors, "deployments.0.replicas")


class ManifestValidationTest(ValidationTestCase):
    def test_relative_path_is_resolved_against_base_dir(self):
        path = self.write("manifests/app.yaml", MANIFEST)
        cfg = self.assert_valid(
            {
                "clusters": [
                    cluster(
                        namespaces=[{"name": "ns", "manifests": ["manifests/app.yaml"]}]
                    )
                ]
            }
        )
        manifest = cfg.clusters[0].namespaces[0].manifests[0]
        self.assertEqual(manifest.path, path)
        self.assertEqual([o["kind"] for o in manifest.objects], ["ConfigMap", "Widget"])

    def test_absolute_path_and_long_form(self):
        path = self.write("app.yaml", MANIFEST)
        cfg = self.assert_valid({"clusters": [cluster(manifests=[{"path": path}])]})
        self.assertEqual(cfg.clusters[0].manifests[0].path, path)

    def test_relative_path_in_config_file(self):
        self.write("app.yaml", MANIFEST)
        config = self.write(
            "conf/config.yaml",
            "clusters:\n  - name: a\n    runtime: kind\n    manifests: [../app.yaml]\n",
        )
        cwd = os.getcwd()
        try:
            # Resolved against the config file, not the working directory
            os.chdir(REPO_ROOT)
            cfg = validate_config_file(config)
        finally:
            os.chdir(cwd)
        self.assertIsNotNone(cfg)
        self.assertEqual(
            cfg.clusters[0].manifests[0].path, os.path.join(self.tmp_dir, "app.yaml")
        )

    def test_missing_file(self):
        errors = self.validation_errors(
            {"clusters": [cluster(manifests=["nope.yaml"])]}
        )
        self.assert_error(errors, "clusters.0.manifests.0", "manifest file not found")

    def test_invalid_yaml(self):
        self.write("bad.yaml", "a: [b\n")
        errors = self.validation_errors({"clusters": [cluster(manifests=["bad.yaml"])]})
        self.assert_error(errors, "cannot read manifest")

    def test_empty_manifest(self):
        self.write("empty.yaml", "---\n")
        errors = self.validation_errors(
            {"clusters": [cluster(manifests=["empty.yaml"])]}
        )
        self.assert_error(errors, "contains no objects")

    def test_objects_without_identity(self):
        self.write("m.yaml", "kind: ConfigMap\nmetadata: {}\n---\n- a\n")
        errors = self.validation_errors({"clusters": [cluster(manifests=["m.yaml"])]})
        self.assert_error(errors, "(document 0): missing apiVersion, metadata.name")
        self.assert_error(errors, "(document 1): expected a mapping")

    def test_list_objects_are_rejected(self):
        self.write("m.yaml", "apiVersion: v1\nkind: List\nmetadata: {name: l}\n")
        errors = self.validation_errors({"clusters": [cluster(manifests=["m.yaml"])]})
        self.assert_error(errors, "List objects are not supported")

    def test_builtin_objects_are_validated(self):
        self.write(
            "m.yaml",
            "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: web}\n"
            "spec:\n  template:\n    spec:\n      containers: [{image: x, foo: 1}]\n",
        )
        errors = self.validation_errors({"clusters": [cluster(manifests=["m.yaml"])]})
        self.assert_error(errors, "(Deployment web)", "spec.selector: field required")
        self.assert_error(errors, "spec.template.spec.containers.0.foo: unknown field")
        self.assert_error(
            errors, "spec.template.spec.containers.0.name: field required"
        )


class ToolValidationTest(ValidationTestCase):
    def test_tool_referring_to_unknown_cluster(self):
        errors = self.validation_errors(
            {
                "clusters": [cluster("a")],
                "tools": [{"type": "registry_proxy", "spec": {"clusters": ["b"]}}],
            }
        )
        self.assert_error(errors, "tools.0 (registry_proxy)", "unknown clusters ['b']")

    def test_unknown_tool(self):
        errors = self.validation_errors({"clusters": [cluster()], "tools": ["nope"]})
        self.assert_error(errors, "tools.0")


class ExamplesTest(unittest.TestCase):
    def test_examples_are_valid(self):
        examples = sorted(glob.glob(os.path.join(REPO_ROOT, "examples", "*.yaml")))
        self.assertTrue(examples)
        for path in examples:
            with self.subTest(example=os.path.basename(path)):
                self.assertIsNotNone(validate_config_file(path))


if __name__ == "__main__":
    unittest.main()
