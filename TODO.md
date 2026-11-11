# TODO

| File | Comment | Category |
|---|---|---|
| [core/input.py](core/input.py) | `default` should just be spread into the clusters, without being validated on its own (`CommonConfig`). | Improvement |
| [core/namespaces.py](core/namespaces.py) | Move the resource creation logic (namespaces, deployments, services, pods) to the runtime. | Refactoring |
| [main.py](main.py) | Hide the output of the external commands (liqoctl, kind, ...) unless `--log-level DEBUG`. | Improvement |
| [tools/base.py](tools/base.py) | Add more hooks (e.g. `after_namespace_creation`, `after_deployment_creation`). | Feature |
| [main.py](main.py) | Ask before deleting the existing clusters, with a `--force` flag to skip the confirmation. | Feature |
| [core/input.py](core/input.py) | Kubernetes objects are validated client-side only (client models): constraints checked by the API server (enum values, label syntax, ...) show up only when the objects are created. A server-side dry-run is not possible before the clusters exist. | Improvement |
