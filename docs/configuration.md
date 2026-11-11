# Configuration reference

The configuration is a YAML file with three top-level fields:

| Field | Required | Description |
|---|---|---|
| `clusters` | yes | List of [clusters](#clusters). |
| `default` | no | Any cluster field except `name`, copied into every cluster that does not set it. |
| `tools` | no | List of [tools](#tools) installed on top of the clusters. |

Unknown fields are rejected everywhere, and the whole file (including the
Kubernetes specs and the manifests) is validated before any cluster is
created. Every error is reported with its path, e.g.
`clusters.0.namespaces.0.pods.0.spec: containers.0.name: field required`.

## `default`

Shallow merge: a field set in a cluster replaces the whole `default` field
(e.g. a cluster `cni` replaces the default `cni`, spec included). An error in
`default` is reported once, as `default.<field>`, not for every cluster.

```yaml
default:
  runtime: k3d
  nodes: 2
clusters:
  - name: rome          # k3d, 2 nodes
  - name: milan
    nodes: 3            # k3d, 3 nodes
```

## Typed components

`runtime`, `cni` and every tool share the same structure:

```yaml
cni:
  type: calico          # component name
  spec:                 # optional, parameters of the component
    dataplane: BPF
```

`cni: calico` is a shorthand for `cni: {type: calico}` (all parameters at
their defaults).

## Clusters

| Field | Default | Description |
|---|---|---|
| `name` | required | Unique cluster name: RFC 1123 label (lowercase letters, digits, `-`). |
| `runtime` | required | [Runtime](#runtimes) creating the cluster. |
| `cni` | `flannel` | [CNI](#cnis). |
| `nodes` | `1` | Number of nodes; the first is the control plane. The i-th node is labeled `tier=worker-<i>`. |
| `cluster_cidr` | `10.244.0.0/16` | Pod CIDR (IPv4). |
| `service_cidr` | `10.96.0.0/16` | Service CIDR (IPv4). |
| `manifests` | `[]` | [Manifests](#manifests) applied to the cluster. |
| `namespaces` | `[]` | [Namespaces and their workloads](#namespaces-and-workloads). |

`cluster_cidr` and `service_cidr` must not overlap, and `cluster_cidr` must
contain a /24 for every node (more constraints may come from the CNI, see
below).

## Runtimes

### `kind`

| Parameter | Default | Description |
|---|---|---|
| `image` | `kindest/node:v1.30.0` | Node image; sets the Kubernetes version. |

### `k3d`

| Parameter | Default | Description |
|---|---|---|
| `image` | `docker.io/rancher/k3s:v1.30.2-k3s2` | Node image; sets the Kubernetes version. |
| `disable` | `[]` | k3s components not to deploy: `traefik`, `servicelb`, `metrics-server`, `local-storage`. `servicelb` provides the `LoadBalancer` services. |

### `minikube`

| Parameter | Default | Description |
|---|---|---|
| `kubernetes_version` | `1.30.0` | Kubernetes version. |
| `driver` | `kvm2` | `kvm2` or `docker`. |
| `container_runtime` | `containerd` | `containerd`, `cri-o`, `docker`. |
| `network` | `testbenchnet` (kvm2), `kind` (docker) | Network of the nodes. Clusters must share a network to reach each other. |
| `cpus` | minikube default | CPUs per node (≥ 2). |
| `memory` | minikube default | Memory per node, e.g. `4g`. |
| `disk_size` | minikube default | Disk per node, e.g. `20g`. |

kind, k3d and minikube with the docker driver share the `kind` docker
network, so their clusters can reach each other.

## CNIs

The CNI is always installed by the testbench (the runtime built-in CNI is
disabled), so the same CNI gets the same version and parameters on every
runtime. Common parameters:

| Parameter | Default | Description |
|---|---|---|
| `version` | per CNI | CNI version, without the leading `v` (e.g. `1.18.6`). |
| `wait` | `true` | Wait for the CNI, the nodes and CoreDNS to be ready. |
| `timeout` | `600` | Seconds to wait. |

### `flannel`

| Parameter | Default | Description |
|---|---|---|
| `version` | `0.28.10` | |
| `backend` | `vxlan` | `vxlan`, `host-gw`, `wireguard`. |
| `iface` | default-route interface | Interface used for the inter-node traffic. |
| `enable_nftables` | `false` | Use nftables instead of iptables for the masquerading rules. |

### `calico`

| Parameter | Default | Description |
|---|---|---|
| `version` | `3.30.3` | |
| `dataplane` | `Iptables` | `Iptables`, `Nftables`, `BPF` (BPF replaces kube-proxy). |
| `encapsulation` | `VXLAN` | `VXLAN`, `VXLANCrossSubnet`, `IPIP`, `IPIPCrossSubnet`, `None`. |
| `block_size` | `26` | Size of the per-node IP blocks (20–32, not larger than `cluster_cidr`). |
| `nat_outgoing` | `true` | NAT for traffic leaving the pod network. |
| `mtu` | auto | 576–9000. |
| `observability` | `true` | Install the Calico API server, Goldmane (flow aggregator) and Whisker (UI). |

### `cilium`

Requires the `cilium` CLI.

| Parameter | Default | Description |
|---|---|---|
| `version` | `1.18.6` | |
| `kube_proxy_replacement` | `false` | Replace kube-proxy. |
| `tunnel_protocol` | `vxlan` | `vxlan`, `geneve`. |
| `cluster_pool_mask_size` | `24` | Size of the per-node pod CIDRs (16–30); `cluster_cidr` must contain one per node. |
| `mtu` | auto | 576–9000. |
| `devices` | auto | Interfaces Cilium attaches to. |

## Namespaces and workloads

Created after the clusters and the tools are ready. Per cluster, in order:
the namespaces, the cluster `manifests`, then for each namespace its
`manifests`, `deployments`, `services` and `pods`.

```yaml
namespaces:
  - name: web
    manifests: [manifests/app.yaml]
    deployments:
      - name: nginx
        replicas: 2
        pod_spec:
          containers: [{name: nginx, image: nginx:1.27}]
    services:
      - name: nginx
        spec:
          selector: {app: nginx}
          ports: [{port: 80}]
    pods:
      - name: client
        spec:
          containers: [{name: client, image: busybox, command: [sleep, infinity]}]
```

| Object | Fields | Notes |
|---|---|---|
| namespace | `name`, `manifests`, `deployments`, `services`, `pods` | `name`: RFC 1123 label. |
| deployment | `name`, `replicas` (default 1), `pod_spec` | `pod_spec` is a Kubernetes `PodSpec`; the pods are labeled `app: <name>`. |
| service | `name`, `spec` | `spec` is a Kubernetes `ServiceSpec`. `name`: DNS-1035 label. |
| pod | `name`, `spec` | `spec` is a Kubernetes `PodSpec`; the pod is labeled `app: <name>`. |

Specs use the same camelCase fields as a Kubernetes manifest and are
validated against the models of the official Kubernetes Python client:
unknown fields, wrong types and missing required fields are rejected.
Numbers are accepted where a string is expected (e.g. `cpu: 1`). Names must
be unique per kind within a namespace; namespace names within a cluster.

Validation is client-side: constraints enforced only by the API server (e.g.
enum values) are reported when the objects are created.

## Manifests

Multi-document YAML files, applied with server-side apply. Allowed at
cluster level and namespace level:

```yaml
manifests:
  - crds.yaml                   # relative to the configuration file
  - path: /opt/manifests/db.yaml   # absolute
```

- Namespaced objects without `metadata.namespace` go to the namespace that
  lists the manifest (`default` for cluster-level manifests).
- Every object needs `apiVersion`, `kind` and `metadata.name`. Built-in
  resources (e.g. `apps/v1 Deployment`) are validated like the workload
  specs; custom resources are not.
- Custom resources can follow their CRD in the same file: resources not yet
  served by the API server are retried for up to 2 minutes.

## Tools

### `registry_proxy`

Caching registry proxy
([rpardini/docker-registry-proxy](https://github.com/rpardini/docker-registry-proxy))
shared by every cluster: the nodes of the selected clusters pull their images
through it. Cache and CA persist across runs in `<out-dir>/registry-proxy`.

| Parameter | Default | Description |
|---|---|---|
| `clusters` | all | Clusters whose nodes use the proxy. |
| `image` | `rpardini/docker-registry-proxy:0.6.5` | Proxy image. |
| `registries` | `registry.k8s.io`, `quay.io`, `ghcr.io`, `gcr.io` | Registries cached besides Docker Hub. |
| `manifest_cache` | `true` | Cache the image manifests too (tags are not re-resolved upstream). |
| `auth` | `[]` | Upstream credentials: `registry`, `username`, `password_env` (name of the environment variable holding the password). |
| `extra_no_proxy` | `[]` | Hosts, domain suffixes (`.example.com`) or CIDRs the nodes reach directly. |
| `host_port` | `3128` | Port published on the host, only when a selected cluster is outside the docker network (minikube `kvm2`). Published on all interfaces. |
| `timeout` | `120` | Seconds to wait for the proxy. |

```yaml
tools:
  - type: registry_proxy
    spec:
      auth:
        - registry: auth.docker.io
          username: myuser
          password_env: DOCKERHUB_TOKEN
```

### `liqo`

Installs [Liqo](https://liqo.io) with `liqoctl` (must be in `PATH`), peers
the clusters and offloads namespaces. Supported runtimes: kind, k3d,
minikube.

| Parameter | Default | Description |
|---|---|---|
| `installations` | `[]` | Clusters where Liqo is installed: `cluster`, `version`, `offloadings`. |
| `installations[].version` | `liqoctl` version | A release (`v1.3.0`), `latest`, or `<repo-url>@<commit-sha>`. |
| `installations[].offloadings` | `[]` | Namespaces of the cluster offloaded to its providers (created if missing). |
| `peerings` | `[]` | `[consumer, provider]` pairs; both clusters need an installation. |
| `install_retry` | see below | Retry policy of `liqoctl install`. |

`install_retry`: `attempts` (10), `initial_delay` (5 s), `max_delay`
(120 s), `multiplier` (2), `jitter` (0.1, ±10%).

```yaml
tools:
  - type: liqo
    spec:
      installations:
        - cluster: rome
          offloadings: [shared]
        - cluster: milan
      peerings:
        - [rome, milan]
```

Pods are scheduled on the provider through the Liqo virtual node, e.g. with
`nodeSelector: {liqo.io/remote-cluster-id: milan}`.

On the clusters where it is installed, Liqo adapts the CNI
([Liqo docs](https://docs.liqo.io/en/v1.3.0/installation/install.html)):

| CNI | Configuration |
|---|---|
| all | CNI agents kept off the Liqo virtual nodes. |
| `calico` | `liqo.*` interfaces excluded from the node address autodetection. |
| `cilium` | `devices: [eth+]` unless set. With `kube_proxy_replacement`, offloaded pods cannot be exposed through NodePort/LoadBalancer services. |
| `flannel` | none |

The gateway of the provider is exposed through a `LoadBalancer` service on
k3d (unless `servicelb` is disabled), through a `NodePort` otherwise.
