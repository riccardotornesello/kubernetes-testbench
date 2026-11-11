# Kubernetes testbench

Creates a set of local Kubernetes clusters from a single YAML file: it
creates the clusters, installs their CNI, installs tools on top of them (e.g.
Liqo, with peerings between clusters) and finally deploys the declared
workloads.

Every run starts from scratch: clusters with the same names are deleted and
recreated.

## Requirements

- Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/)
- Docker
- The CLI of the runtimes in use: `kind`, `k3d`, `minikube`

## Usage

```bash
uv run python main.py <config.yaml> [--out-dir out] [--log-level INFO] [--log-format console]
```

| Argument | Description |
|---|---|
| `config.yaml` | Configuration file (see [examples](#examples)). |
| `--out-dir` | Directory of the generated files: kubeconfigs, caches, registry proxy data. Default `out`. |
| `--log-level` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR`. `DEBUG` also shows the external commands (kind, liqoctl, ...) and their output; otherwise the output is shown only when a command fails. |
| `--log-format` | `console` (default) or `json` (one object per line). |

The kubeconfig of each cluster is written to `<out-dir>/kubeconfigs/<name>.yaml`:

```bash
kubectl --kubeconfig out/kubeconfigs/demo.yaml get nodes
```

## Configuration

```yaml
default:              # optional: fields copied into every cluster that does not set them
  runtime: k3d

clusters:
  - name: rome
    runtime: kind     # required (here or in `default`)
    cni: flannel      # default flannel
    nodes: 2          # default 1
    namespaces:       # optional workloads
      - name: demo
        manifests: [app.yaml]
        deployments: [...]
        services: [...]
        pods: [...]

tools:                # optional
  - registry_proxy
```

The whole configuration, including the Kubernetes specs of the workloads, is
validated before any cluster is touched. Full reference:
[docs/configuration.md](docs/configuration.md).

## Available components

| Runtime | Description |
|---|---|
| `kind` | Clusters of docker containers ([kind](https://kind.sigs.k8s.io/)). |
| `k3d` | k3s clusters of docker containers ([k3d](https://k3d.io/)), with `LoadBalancer` services. |
| `minikube` | VMs (`kvm2` driver) or docker containers ([minikube](https://minikube.sigs.k8s.io/)). |

| CNI | Description | Requirements |
|---|---|---|
| `flannel` | Default. Backends `vxlan`, `host-gw`, `wireguard`. | - |
| `calico` | Through the Tigera operator; `Iptables`, `Nftables` or `BPF` dataplane. | - |
| `cilium` | Optional kube-proxy replacement. | `cilium` CLI |

| Tool | Description | Requirements |
|---|---|---|
| `registry_proxy` | Caching proxy for container images, shared by the clusters: faster recreations. Recommended to avoid the Docker Hub pull rate limit, but **not secure**: the nodes trust its CA (it intercepts their TLS traffic), and with minikube `kvm2` it is published on every host interface, reachable from the LAN. Use it only on trusted networks. | - |
| `liqo` | Installs [Liqo](https://liqo.io) on the selected clusters, peers them and offloads namespaces. | `liqoctl` |

## Networks

| Network | Created by | Used by |
|---|---|---|
| `kind` docker network | the testbench, if missing | kind, k3d and minikube (`docker` driver) nodes, registry proxy |
| `testbenchnet` libvirt network | minikube, if missing | minikube (`kvm2` driver) VMs |

- **kind** always uses the `kind` docker network, so k3d and minikube
  (`docker` driver) join it too: their clusters reach each other directly.
- **k3d** clusters expose `LoadBalancer` services (k3s ServiceLB) on the
  node addresses of the `kind` network.
- **minikube `kvm2`** VMs are attached to the libvirt network set by the
  `network` parameter (default `testbenchnet`), shared by the kvm2 clusters.
  They reach the registry proxy through a port published on the host.
- Clusters on the docker network and clusters on a libvirt network cannot
  reach each other (blocked by the Docker and libvirt firewall rules): e.g.
  they cannot be peered with Liqo.

Pod and service CIDRs (`cluster_cidr`, `service_cidr`) are per cluster;
peered clusters do not need different ones, but distinct CIDRs avoid the
Liqo address remapping.

**WARNING**: minikube `kvm2` clusters cannot reach other clusters on the docker network, so Liqo peerings between them are not possible. Use minikube `docker` driver instead. This should be addressed in a future release of this tool.

## Examples

| File | Content |
|---|---|
| [examples/minimal.yaml](examples/minimal.yaml) | One kind cluster, all defaults. |
| [examples/workloads.yaml](examples/workloads.yaml) | One k3d cluster with Calico, a deployment, a service, a pod and a [YAML manifest](examples/manifests/hello.yaml). |
| [examples/liqo_k3d.yaml](examples/liqo_k3d.yaml) | Two k3d clusters peered with Liqo, a pod offloaded to the provider, registry proxy. |
| [examples/liqo_minikube.yaml](examples/liqo_minikube.yaml) | Two minikube VMs with Cilium, peered with Liqo. |

## Documentation

- [docs/configuration.md](docs/configuration.md): every configuration field,
  runtime, CNI and tool parameter.
- [docs/development.md](docs/development.md): architecture, execution flow,
  tool hooks, how to add a runtime, CNI or tool.
- [TODO.md](TODO.md): known limitations and planned work.

## License

[Apache License 2.0](LICENSE).
