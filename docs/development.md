# Developer guide

How the testbench works internally and how to extend it with new runtimes,
CNIs and tools. For the configuration format see
[configuration.md](configuration.md).

## Project layout

| Path | Content |
|---|---|
| `main.py` | Entry point: CLI arguments and the [execution flow](#execution-flow). |
| `core/input.py` | Configuration models (pydantic) and validation. |
| `core/modules.py` | Discovery of runtimes, CNIs and tools. |
| `core/workloads.py` | Creation of namespaces, manifests and workloads. |
| `core/networking.py` | Node networks: addressing plan, host checks, creation, routing. |
| `core/summary.py` | Node addresses shown at the end of the run. |
| `core/log.py`, `core/paths.py` | Structured logging, output directory. |
| `runtimes/` | One module per runtime, base class in `runtimes/base.py`. |
| `cnis/` | One module per CNI, base class in `cnis/base.py`. |
| `tools/` | One module per tool, base class in `tools/base.py`. |
| `utils/kubernetes_utils.py` | Kubernetes API helpers (apply, wait, create). |
| `utils/kubernetes_models.py` | Validation of dicts against the Kubernetes client models. |
| `utils/commands.py` | `run_command`: execution of the external commands. |
| `utils/docker_utils.py`, `utils/libvirt_utils.py` | Docker containers and networks, libvirt networks. |
| `utils/firewall.py` | iptables chains routing the testbench networks. |

## Components

Runtimes, CNIs and tools are discovered from their directory: every module
exporting `module` (the class) and `spec` (its pydantic parameter model) is
available under the module file name.

```python
# cnis/flannel.py -> `cni: flannel`
module = FlannelCNI
spec = FlannelCNISpec
```

The spec models subclass `BaseRuntimeSpec`, `BaseCNISpec` or `BaseToolSpec`
(unknown fields rejected) and are validated as the `spec` of
`{type: <name>, spec: {...}}`. Specs can also validate themselves against the
rest of the configuration:

- `BaseCNISpec.validate_cluster(cluster_cidr, service_cidr, nodes)`, e.g.
  Calico checks that `block_size` fits in `cluster_cidr`.
- `BaseToolSpec.validate_clusters(cluster_names)`, e.g. Liqo checks that
  installations and peerings refer to existing clusters
  (`tools.base.check_known_clusters`).

Both raise `ValueError`, reported as a configuration error.

### Runtimes

A runtime (`runtimes/base.py`, `BaseRuntime`) creates a cluster of
`settings.nodes` nodes with the given pod/service CIDRs, labels the i-th node
`tier=worker-<i>`, and writes a kubeconfig with only that cluster to
`utils.kubeconfig.get_kubeconfig_location(name)`. The cluster is always
created **without** its built-in CNI, and without kube-proxy when
`cni.replaces_kube_proxy`. Its nodes are attached to `settings.network`
(`NodeNetwork`: `name`, `cidr`, `gateway`, `pool`), created by the
testbench before `init_cluster` with the type given by `network_type`.

| Member | Description |
|---|---|
| `init_cluster()` | Abstract. Creates the cluster. |
| `cleanup()` | Abstract. Deletes the cluster; must not fail if it does not exist. |
| `get_api_server_address()` | Abstract. API server URL reachable from the nodes of the other clusters. |
| `check_dependencies()` | Raises if a dependency (e.g. the CLI) is missing. |
| `install_cni_plugins(plugins)` | Copies the reference CNI plugins into the nodes, if the image lacks them. |
| `load_kernel_modules(modules)` | Loads kernel modules on the nodes (container-based runtimes). |
| `network_type` | `docker` (default) or `libvirt`: type of the network created for the nodes. |
| `provides_load_balancer` | Whether `LoadBalancer` services get a reachable address. |
| `kernel_wireguard` | Whether the node kernel supports WireGuard (default `True`; `False` for minikube `kvm2`): if not, Liqo is installed with the userspace WireGuard implementation. |
| `set_node_proxy(NodeProxy)` | Generic option set by tools before `init_cluster`: the container runtime of the nodes pulls through an HTTP(S) proxy, trusting its CA. Helpers: `node_no_proxy()`, `node_proxy_setup_script(service)`. |

Runtimes contain no tool-specific logic: tools customize them only through
generic options like `set_node_proxy`.

### CNIs

A CNI (`cnis/base.py`, `BaseCNI`) installs itself on an existing cluster.
`runtime.install_cni()` loads `REQUIRED_KERNEL_MODULES`, copies
`REQUIRED_CNI_PLUGINS`, calls `cni.install_cni()` and, if `spec.wait`,
`cni.wait_ready()` (waits for `WORKLOADS`, the nodes and CoreDNS).

| Member | Description |
|---|---|
| `install_cni()` | Abstract. Installs the CNI, applying the tool customizations. |
| `replaces_kube_proxy` | Whether the cluster must be created without kube-proxy. |
| `WORKLOADS` | `{namespace: [names] or None}` waited for by `wait_ready`. |
| `REQUIRED_CNI_PLUGINS`, `REQUIRED_KERNEL_MODULES` | Prerequisites provided by the runtime. |

CNIs contain no tool-specific configuration: tools register it on the CNI
instance in their `pre_cni_install` hook, only for the clusters they care
about.

- `cni.add_node_selector_requirement(req)`: generic, supported by every CNI.
  Restricts the CNI node agents to the nodes matching a Kubernetes
  `NodeSelectorRequirement` (`install_cni` uses `node_affinity()`).
- `cni.add_patch(fn)`: CNI-specific. `fn` receives the configuration
  generated by the CNI right before it is installed, and modifies it in place
  (returning `None`) or returns a new one. Patches run in registration order
  (`apply_patches`).

| CNI | Configuration passed to patches |
|---|---|
| `flannel` | Objects of the `kube-flannel.yml` manifest (`list[dict]`), already customized with the spec. |
| `calico` | Tigera operator resources: `Installation`, `APIServer`, `Goldmane`, `Whisker` (`list[dict]`). |
| `cilium` | Helm values (`dict`). |

`cnis.base.find_object(objects, kind, name)` locates an object in a list.

```python
def pre_cni_install(self, settings, runtime, cni):
    cni.add_node_selector_requirement({"key": "liqo.io/type", "operator": "DoesNotExist"})
    if isinstance(cni, CalicoCNI):
        cni.add_patch(_calico_skip_liqo_interfaces)
```

### Tools

A tool (`tools/base.py`, `BaseTool`) acts on the clusters through hooks
called by `main.py`. Every tool receives every hook, for every cluster: a
tool acting only on some clusters filters by itself (e.g. Liqo by its
`installations`). Within a hook, tools run in the order of `tools:`.

| Hook | Called | Arguments | Typical use |
|---|---|---|---|
| `check_dependencies()` | once | | Fail early on missing CLIs or env vars. |
| `before_all_operations` | once, before any cluster | `all_settings`, `all_runtimes`, `all_cnis` (by cluster name) | Shared infrastructure (registry proxy container, on the infrastructure network if `uses_infra_network`). |
| `pre_cluster_init` | per cluster | `settings`, `runtime`, `cni` | Runtime options (`runtime.set_node_proxy`). No kubeconfig yet. |
| `post_cluster_init` | per cluster | same | Cluster up, kubeconfig written, no CNI. |
| `pre_cni_install` | per cluster | same | CNI customizations (`add_node_selector_requirement`, `add_patch`). |
| `post_cni_install` | per cluster | same | Cluster ready (`liqoctl install`). |
| `after_all_operations` | once, after every cluster | same as `before_all_operations` | Cross-cluster operations (peering, offloading). |

Workloads from the configuration are created after `after_all_operations`.

## Execution flow

Steps executed by `main.py`. Blue = Kubernetes/cluster operations, orange =
tool hooks, grey = internal steps.

```mermaid
flowchart TD
    A([Start: python main.py config.yaml]) --> B[Load and validate YAML config<br/>merge defaults, validate specs and manifests]
    B --> C[Instantiate tools, runtimes and CNIs]
    C --> C2[Host checks<br/>Docker firewall backend, Fedora warning]
    C2 --> D[Check runtime, CNI and tool dependencies<br/>sudo -v if firewall rules are needed]
    D --> E[Delete existing clusters<br/>runtime.cleanup for every cluster]
    E --> E2[Ensure the networks<br/>one per cluster, infrastructure if used]
    E2 --> F[tool.before_all_operations<br/>once in total<br/>registry_proxy: start proxy container]
    F --> F2[Apply the firewall rules<br/>routing between the networks]

    F2 --> L{{For each cluster}}
    L --> H[tool.pre_cluster_init<br/>registry_proxy: runtime.set_node_proxy]
    H --> I[Create cluster<br/>runtime.init_cluster + kubeconfig]
    I --> J[tool.post_cluster_init]
    J --> J2[tool.pre_cni_install<br/>Liqo: CNI customizations]
    J2 --> K[Install CNI<br/>kernel modules + CNI plugins + install<br/>wait until ready]
    K --> M[tool.post_cni_install<br/>Liqo: liqoctl install]
    M --> N{More clusters?}
    N -- yes --> L
    N -- no --> O[tool.after_all_operations<br/>once in total<br/>Liqo: liqoctl peer + offload namespaces]

    O --> P[For every cluster: namespaces, cluster manifests,<br/>then namespace manifests, deployments, services, pods]
    P --> R[Print the node addresses of every cluster]
    R --> Q([End])

    classDef internal fill:#e0e0e0,stroke:#616161,color:#000
    classDef k8s fill:#bbdefb,stroke:#1565c0,color:#000
    classDef tool fill:#ffe0b2,stroke:#ef6c00,color:#000

    class A,B,C,C2,D,F2,L,N,R,Q internal
    class E,E2,I,K,P k8s
    class F,H,J,J2,M,O tool
```

Clusters are created sequentially, in configuration order. Any exception
stops the run (clusters already created are left in place).

## Built-in tools

### registry_proxy

`before_all_operations` starts (or reuses, if its configuration did not
change) the [docker-registry-proxy](https://github.com/rpardini/docker-registry-proxy)
container on the infrastructure network (`uses_infra_network`), with cache
and CA in `<out-dir>/registry-proxy`. Every node reaches it through the host
routing; it is never published on the host.
`pre_cluster_init` calls `runtime.set_node_proxy(...)` on the selected
clusters; kind and minikube configure the proxy on every node after creation,
k3d at creation.

### liqo

- `before_all_operations`: rejects clusters on unsupported runtimes.
- `pre_cni_install`: keeps the CNI agents off the Liqo virtual nodes; on
  Calico excludes the `liqo.*` interfaces from the node address
  autodetection, on Cilium sets `devices: [eth+]` unless set.
- `post_cni_install`: `liqoctl install`, retried with exponential backoff
  (`install_retry`).
- `after_all_operations`: `liqoctl peer` for each pair, then
  `liqoctl offload namespace`.

| Runtime | `liqoctl install` provider | Notes |
|---|---|---|
| `kind` | `kind` | |
| `k3d` | `k3s` | `--api-server-url` (control plane address on the cluster network), `--pod-cidr`, `--service-cidr`. |
| `minikube` | `kubeadm` | CIDRs read from the kube-controller-manager; API server URL set to the node address (`--set-string apiServer.address=...`). |

The gateway of the provider is exposed through a `LoadBalancer` service if
`runtime.provides_load_balancer` (k3d unless `servicelb` is disabled),
`NodePort` otherwise.

On runtimes without `kernel_wireguard` (minikube `kvm2`), `liqoctl install`
gets `--set networking.gatewayTemplates.wireguard.implementation=userspace`:
with the kernel implementation the gateway fails to create `liqo-tunnel`
("operation not supported") and crash-loops.

## Networks

`core/networking.py`:

- `plan_networks` assigns the /24s of `network.cidr`: the first to the
  infrastructure network (`testbench_infra`), then one per cluster
  (`testbench-<name>`), and resolves `network.links` (default: every pair).
- `check_host` runs first: with docker networks it warns on Fedora
  (`/etc/os-release`) and requires the iptables firewall backend of Docker
  (`docker info`).
- `ensure_networks` creates the docker networks
  (`docker_utils.ensure_docker_network`, `nat-unprotected` gateway mode) and
  the libvirt ones (`libvirt_utils.ensure_libvirt_network`, `open` forward
  mode, DHCP pushing the route to the pool and no default route), after the
  old clusters are deleted. A network with a different configuration is
  recreated.
- `apply_firewall` (only if `uses_firewall`: more than one network, or a
  libvirt one) builds the chains of `utils/firewall.py` and applies them
  with `iptables` (through `sudo` if not root), after
  `before_all_operations` and before the first cluster.

minikube is started with `--cni <out>/minikube/<name>-cni-placeholder.yaml`
(a "custom CNI" holding only a ConfigMap), not `--cni false`: minikube
refuses `false` with the containerd and cri-o runtimes, and with `false` it
keeps its default bridge CNI (10.244.0.0/16) active until the real CNI is
installed.

minikube `kvm2` also sets the route to the pool on every VM
(`ip route replace <pool> via <gateway>`), since not every DHCP client
applies the classless static route option.

## Configuration validation

`core/input.py` builds the configuration models with pydantic:

- `RootConfig.merge_defaults` copies the `default` fields into the clusters
  before validation. `validate_data` also validates `default` on its own,
  as a partial `ClusterConfig` (`_validate_default`: only the errors on the
  fields it sets), and drops the cluster errors on inherited fields that
  already failed in `default`: each error is reported once, where it is
  written. Cross-field checks (CIDRs, CNI vs CIDR) run per cluster only.
- `runtime`, `cni` and tools are discriminated unions on `type`, built from
  the discovered spec models (`_typed_config`).
- Pod and service specs are converted by `utils/kubernetes_models.to_model`
  into the official Kubernetes client models (`V1PodSpec`, `V1ServiceSpec`).
  The client models only check required fields and their deserializer drops
  unknown fields, so `to_model` walks `openapi_types`/`attribute_map`
  itself, collecting every unknown field, type error and missing required
  field with its path.
- Manifests are resolved against `base_dir` (directory of the configuration
  file, passed in the pydantic validation context), loaded, and their built-in
  objects validated with `to_model` (`model_name_for` maps
  `apiVersion`/`kind` to the client model).

## Adding a component

1. Create `runtimes/<name>.py`, `cnis/<name>.py` or `tools/<name>.py`.
2. Define the spec model (subclass of the base spec) and the class
   implementing the abstract methods / hooks.
3. Export them as `module` and `spec`.
4. Document the parameters in [configuration.md](configuration.md) and the
   component in the [README](../README.md).

Minimal tool:

```python
from pydantic import Field

from tools.base import BaseTool, BaseToolSpec, check_known_clusters


class HelloToolSpec(BaseToolSpec):
    clusters: list[str] = Field(default_factory=list)

    def validate_clusters(self, clusters: list[str]) -> None:
        check_known_clusters(self.clusters, clusters)


class HelloTool(BaseTool[HelloToolSpec]):
    def post_cni_install(self, settings, runtime, cni) -> None:
        if settings.name in self.spec.clusters:
            print(f"cluster {settings.name} ready, kubeconfig: {cni.kubeconfig}")


module = HelloTool
spec = HelloToolSpec
```

## Logging and output

- `log = core.log.get_logger(__name__)`, then
  `log.info("Installing Liqo", cluster=name)` or `log.bind(cluster=name)`.
  Logs go to stderr (`console` or `json` format).
- External commands run through `utils.commands.run_command`, never
  `subprocess.run` directly. With `--log-level DEBUG` the command is logged
  and its output streamed to stdout; otherwise the output is captured and
  its last lines are included in the `CommandError` raised on failure.
  `capture=True` returns the output (`result.stdout`) instead, e.g. to parse
  it.
- Generated files go to `--out-dir` (default `out`); in code use
  `core.paths.output_path(...)`.

## Tests

Unit tests in `tests/`, written with the standard `unittest` module (no
extra dependency; pytest can run them too). They need neither Docker nor a
cluster: external commands and the Kubernetes API are mocked. Run them from
the repository root (components are discovered relative to it):

```bash
uv run python -m unittest            # all
uv run python -m unittest tests.test_input -v
```

| File | Covers |
|---|---|
| `test_input.py` | Configuration validation: clusters, `default` (errors reported once), workloads, names, manifests (relative/absolute paths), tools, examples. |
| `test_kubernetes_models.py` | Conversion and validation against the Kubernetes client models. |
| `test_cnis.py` | CNI configuration generation, tool customizations (affinity, patches), CNI-specific validation. |
| `test_runtimes.py` | kind/k3d configuration, minikube arguments and routes, network types, node proxy. |
| `test_networking.py` | Network plan, firewall rules and their application, host checks, libvirt network, node summary. |
| `test_liqo.py` | Liqo spec validation, CNI customizations, `liqoctl` commands, retries. |
| `test_commands.py` | `run_command`: output capture, errors. |
| `test_retry.py` | Exponential backoff. |
| `test_workloads.py` | Workload creation order, Kubernetes objects, manifest namespaces. |

`tests/helpers.py` provides `make_settings`, `cluster` and
`ValidationTestCase` (validates raw configurations in a temporary directory
and captures the reported errors).

### End-to-end tests

`tests/e2e/` runs the real testbench (`main.py`) and checks the created
clusters through the Kubernetes API. They are skipped unless
`TESTBENCH_E2E=1`: they need Docker and the runtime CLIs, create real
clusters and take minutes.

```bash
TESTBENCH_E2E=1 uv run python -m unittest tests.e2e -v                 # every installed runtime
TESTBENCH_E2E=1 TESTBENCH_E2E_RUNTIMES=kind uv run python -m unittest tests.e2e -v
TESTBENCH_E2E=1 uv run python -m unittest tests.e2e.test_clusters.KindE2E.test_flannel -v
```

| Variable | Description |
|---|---|
| `TESTBENCH_E2E=1` | Enables the tests. |
| `TESTBENCH_E2E_RUNTIMES` | Runtimes to test, comma separated (default: those whose CLI is installed). minikube uses the docker driver. |
| `TESTBENCH_E2E_KEEP=1` | Keeps clusters and files after the tests, for debugging. |

Every scenario runs on each selected runtime (`KindE2E`, `K3dE2E`,
`MinikubeE2E`):

| Test | Checks |
|---|---|
| `test_flannel`, `test_calico`, `test_cilium` | 2-node cluster with the CNI: nodes Ready with their `tier` labels, every pod Ready. `test_cilium` needs the `cilium` CLI. |
| `test_workloads` | Deployment, service, pod pinned to `worker-1`, manifest with a relative path; a Job reaches the service through the cluster DNS. |
| `test_registry_proxy` | Proxy container running, pod image pulled through it (cache not empty). |
| `test_liqo` | Two peered clusters: virtual node on the consumer, offloaded pod running on it. Needs `liqoctl`. |
| `test_minikube_containerd`, `test_minikube_crio`, `test_minikube_docker_runtime` | minikube only: 2-node cluster with the container runtime, healthy, every pod address in the cluster CIDR (none from the minikube default bridge CNI). |
| `test_minikube_kvm2` | minikube only: as above on `kvm2` VMs with Cilium (the runtime of `examples/liqo_minikube.yaml`). Needs `virsh`, `cilium` and `/dev/kvm`; uses a libvirt network, so it applies firewall rules (`sudo`). |
| `test_minikube_kvm2_liqo` | minikube only: `test_liqo` on `kvm2` VMs with Cilium (`examples/liqo_minikube.yaml`): the gateways use userspace WireGuard. Needs `virsh`, `cilium`, `liqoctl`, `/dev/kvm` and `sudo`. |

Every run must exit successfully and log no error (`--log-format json`).
Scenarios with more than one network (`test_registry_proxy`, `test_liqo`)
or with a libvirt network (`test_minikube_kvm2*`) apply firewall rules: run
the tests as root or with a valid `sudo` timestamp (`sudo -v` before).
Clusters are named `tbe2e-<runtime>-<scenario>`, so they never clash with
other clusters, and are deleted at the end of each test. On a failed run the
logs (`stderr` in the failure message, command output in `stdout.log`) are
kept in the reported directory. The registry proxy container has a fixed
name (`testbench-registry-proxy`): `test_registry_proxy` replaces an
existing one and removes it at the end; the next testbench run recreates it.
