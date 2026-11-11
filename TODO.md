# TODO

| File | Comment | Category |
|---|---|---|
| [tools/liqo.py:97](tools/liqo.py#L97) | `cluster_b.runtime == "k3d"` is always false: `all_settings` contains `ClusterConfig` objects, where `.runtime` is a `RuntimeConfig`, not a string. Peering always uses `NodePort`, even on k3d. Fix: `cluster_b.runtime.type.value == "k3d"`. | Bug |
| [tools/liqo.py:123](tools/liqo.py#L123) | `version.split("@")` raises `ValueError` when the version has no `@` (e.g. `v1.0.0`). The `repo@hash` format is neither documented nor validated. | Bug |
| [core/input.py:89](core/input.py#L89) | `merge_defaults` copies only the fields written in the YAML `default` block, not the `CommonConfig` defaults: missing `nodes`/`cache` become `None` (k3d crashes on `nodes - 1`). | Bug |
| [core/input.py:115](core/input.py#L115) | If `cni` is missing, `validate_compatibility` raises a raw `AttributeError` on `cluster.cni.value` instead of a validation error. | Bug |
| [core/input.py:90](core/input.py#L90) | `default: null` in the YAML makes `default_cfg` `None`, so `.items()` crashes. | Bug |
| [runtimes/minikube.py:73](runtimes/minikube.py#L73) | The whole `~/.kube/config` is copied, and `get_api_server_address` ([line 85](runtimes/minikube.py#L85)) reads `clusters[0]`, which may belong to a different cluster. | Bug |
| [runtimes/k3d.py:136](runtimes/k3d.py#L136) | `NO_PROXY='...'`: the single quotes probably end up inside the variable value, breaking the first and last entries. | Bug |
| [runtimes/minikube.py:38](runtimes/minikube.py#L38) | `calico_bpf` is passed to `minikube --cni` as is, but minikube does not accept that value. | Bug |
| [tools/liqo.py:65](tools/liqo.py#L65) | Liqo is installed on minikube with the `kind` provider. Check whether this is intended or `kubeadm`/`generic` should be used. | To verify |
| [tools/base.py:40](tools/base.py#L40) | Type hints `Type[BaseRuntime]` / `Type[BaseCNI]` are wrong: instances are passed, not classes. | Improvement |
| [tools/base.py:80](tools/base.py#L80) | `all_settings` is typed as `Dict[str, Settings]` but actually contains `ClusterConfig` objects. | Improvement |
| [main.py:55](main.py#L55) | Tool specs are validated only when the tool instances are created, after `validate_config_file`: errors show a traceback instead of the formatted message. | Improvement |
| [tools/liqo.py](tools/liqo.py) | Cluster names in `peerings` and `installations` are not validated against the configured clusters: an unknown cluster in `peerings` raises `KeyError` only at the end, after every cluster has been created. | Improvement |
| [tools/liqo.py:150](tools/liqo.py#L150) | `liqoctl install` retries 10 times without any pause. Add `time.sleep` with backoff. | Improvement |
| [main.py:55](main.py#L55) | The local variable `tools` shadows the `tools` dict imported from `core.modules`. Works, but confusing. | Code quality |
| [main.py:75](main.py#L75) | The registry proxy container always starts, even when no cluster has `cache: true`. | Improvement |
| [main.py:12](main.py#L12) | `Settings` is built twice identically in `create_runtime_instance` and `create_cni_instance`. Extract a single helper. | Code quality |
| [cnis/calico_bpf.py](cnis/calico_bpf.py) | Nearly identical to [cnis/calico.py](cnis/calico.py), with the same class name `CalicoCNI`. Use a subclass that overrides only `_gen_config`. | Code quality |
