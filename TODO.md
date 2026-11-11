# TODO

| File | Comment | Category |
|---|---|---|
| [tools/liqo.py:97](tools/liqo.py#L97) | `cluster_b.runtime == "k3d"` is always false: `all_settings` contains `ClusterConfig` objects, where `.runtime` is a `RuntimeConfig`, not a string. Peering always uses `NodePort`, even on k3d. Fix: `cluster_b.runtime.type.value == "k3d"`. | Bug |
| [tools/liqo.py:123](tools/liqo.py#L123) | `version.split("@")` raises `ValueError` when the version has no `@` (e.g. `v1.0.0`). The `repo@hash` format is neither documented nor validated. | Bug |
| [runtimes/minikube.py:73](runtimes/minikube.py#L73) | The whole `~/.kube/config` is copied, and `get_api_server_address` ([line 85](runtimes/minikube.py#L85)) reads `clusters[0]`, which may belong to a different cluster. | Bug |
| [runtimes/k3d.py:136](runtimes/k3d.py#L136) | `NO_PROXY='...'`: the single quotes probably end up inside the variable value, breaking the first and last entries. | Bug |
| [tools/liqo.py:65](tools/liqo.py#L65) | Liqo is installed on minikube with the `kind` provider. Check whether this is intended or `kubeadm`/`generic` should be used. | To verify |
| [tools/base.py:40](tools/base.py#L40) | Type hints `Type[BaseRuntime]` / `Type[BaseCNI]` are wrong: instances are passed, not classes. | Improvement |
| [tools/base.py:80](tools/base.py#L80) | `all_settings` is typed as `Dict[str, Settings]` but actually contains `ClusterConfig` objects. | Improvement |
| [main.py:55](main.py#L55) | Tool specs are validated only when the tool instances are created, after `validate_config_file`: errors show a traceback instead of the formatted message. | Improvement |
| [tools/liqo.py](tools/liqo.py) | Cluster names in `peerings` and `installations` are not validated against the configured clusters: an unknown cluster in `peerings` raises `KeyError` only at the end, after every cluster has been created. | Improvement |
| [tools/liqo.py:150](tools/liqo.py#L150) | `liqoctl install` retries 10 times without any pause. Add `time.sleep` with backoff. | Improvement |
| [main.py:55](main.py#L55) | The local variable `tools` shadows the `tools` dict imported from `core.modules`. Works, but confusing. | Code quality |
| [main.py:75](main.py#L75) | The registry proxy container always starts, even when no cluster has `cache: true`. | Improvement |
