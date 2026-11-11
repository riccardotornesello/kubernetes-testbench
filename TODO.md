# TODO

| File | Comment | Category |
|---|---|---|
| [tools/liqo.py:123](tools/liqo.py#L123) | `version.split("@")` raises `ValueError` when the version has no `@` (e.g. `v1.0.0`). The `repo@hash` format is neither documented nor validated. | Bug |
| [tools/liqo.py:65](tools/liqo.py#L65) | Liqo is installed on minikube with the `kind` provider. Check whether this is intended or `kubeadm`/`generic` should be used. | To verify |
| [tools/base.py:40](tools/base.py#L40) | Type hints `Type[BaseRuntime]` / `Type[BaseCNI]` are wrong: instances are passed, not classes. | Improvement |
| [tools/base.py:80](tools/base.py#L80) | `all_settings` is typed as `Dict[str, Settings]` but actually contains `ClusterConfig` objects. | Improvement |
| [tools/liqo.py:150](tools/liqo.py#L150) | `liqoctl install` retries 10 times without any pause. Add `time.sleep` with backoff. | Improvement |
