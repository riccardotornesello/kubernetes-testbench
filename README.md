## Execution flow

Steps executed by `main.py`. Blue = Kubernetes/cluster configuration, orange = tool hooks, grey = internal steps (config, dependencies, cache).

```mermaid
flowchart TD
    A([Start: python main.py config.yaml]) --> B[Load and validate YAML config<br/>merge defaults into clusters]
    B --> C[Instantiate tools, runtimes and CNIs]
    C --> D[Check runtime and CNI dependencies<br/>for every cluster]
    D --> E[Delete existing clusters<br/>runtime.cleanup for every cluster]
    E --> F[Start registry proxy container]

    F --> L{{For each cluster}}
    L --> G[Set proxy on runtime<br/>if cache is enabled]
    G --> H[tool.pre_cluster_init<br/>once per cluster]
    H --> I[Create cluster<br/>runtime.init_cluster + kubeconfig]
    I --> J[tool.post_cluster_init<br/>once per cluster]
    J --> J2[tool.pre_cni_install<br/>once per cluster]
    J2 --> K[Install CNI<br/>skipped if runtime default CNI]
    K --> M[tool.post_cni_install<br/>once per cluster<br/>Liqo: liqoctl install]
    M --> N{More clusters?}
    N -- yes --> L
    N -- no --> O[tool.after_all_operations<br/>once in total<br/>Liqo: liqoctl peer + offload namespace]

    O --> P[Create namespaces, deployments,<br/>services and pods for every cluster]
    P --> Q([End])

    classDef internal fill:#e0e0e0,stroke:#616161,color:#000
    classDef k8s fill:#bbdefb,stroke:#1565c0,color:#000
    classDef tool fill:#ffe0b2,stroke:#ef6c00,color:#000

    class A,B,C,D,F,G,L,N,Q internal
    class E,I,K,P k8s
    class H,J,J2,M,O tool
```

## TODO

- Move resources creation logic to runtime
- Check types, in some places Type[...] is wrong
- Update the output format, make it cleaner and hide the commands output if verbose is disabled
- Add CNI spec
- The CNI should be optional, if not specified use the default one, otherwise install the specified one
- Improve the hooks system, add more hooks (after_namespace_creation, after_deployment_creation, etc.)
- Check why the cluster does not start without specifying the cidr in the config file
- Ask before deleting the clusters, maybe with a --force flag to skip the confirmation
- The default input should just be spread to the clusters spec, without validation
