# Kubernetes testbench

Creates a set of local Kubernetes clusters from a single YAML file: it
creates the clusters, installs their CNI, installs tools on top of them (e.g.
Liqo, with peerings between clusters) and finally deploys the declared
workloads.

Every run starts from scratch: clusters with the same names are deleted and
recreated (after a confirmation, unless `--force`). `down` deletes them,
together with the shared infrastructure, the networks and the firewall rules.

## Requirements

- Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/)
- Docker, with the iptables firewall backend (see [Compatibility](#compatibility))
- The CLI of the runtimes in use: `kind`, `k3d`, `minikube`; `virsh` for
  minikube `kvm2`
- `iptables` and `sudo` (or running as root), when the host must route
  between the testbench networks (see [Networks](#networks))

## Usage

```bash
uv run python main.py up <config.yaml> [--force] [--out-dir out] [--log-level INFO] [--log-format console]
uv run python main.py down <config.yaml> [--out-dir out] [--log-level INFO] [--log-format console]
```

| Command | Description                                                                                                                                                                                                                                                                                         |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `up`    | Creates the clusters, their CNI, tools and workloads. If some clusters of the configuration already exist, asks for confirmation before deleting and recreating them (without a terminal it stops with an error: use `--force`). `main.py <config.yaml>` is the same as `main.py up <config.yaml>`. |
| `down`  | Deletes the clusters of the configuration and their kubeconfigs, the shared infrastructure (registry proxy container), the testbench networks and the firewall rules. The registry proxy cache stays in `<out-dir>`.                                                                                |

| Argument       | Description                                                                                                                                                                             |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `config.yaml`  | Configuration file (see [examples](#examples)).                                                                                                                                         |
| `--force`      | `up` only: deletes the existing clusters without asking.                                                                                                                                |
| `--out-dir`    | Directory of the generated files: kubeconfigs, caches, registry proxy data. Default `out`.                                                                                              |
| `--log-level`  | `DEBUG`, `INFO` (default), `WARNING`, `ERROR`. `DEBUG` also shows the external commands (kind, liqoctl, ...) and their output; otherwise the output is shown only when a command fails. |
| `--log-format` | `console` (default) or `json` (one object per line).                                                                                                                                    |

The kubeconfig of each cluster is written to `<out-dir>/kubeconfigs/<name>.yaml`:

```bash
kubectl --kubeconfig out/kubeconfigs/demo.yaml get nodes
```

At the end, the addresses of the nodes of every cluster are printed on
stdout (one log record per node with `--log-format json`):

```
rome (kind, network testbench-rome 10.250.1.0/24)
  NODE                ROLES          TIER      IP
  rome-control-plane  control-plane  worker-0  10.250.1.2
  rome-worker         worker         worker-1  10.250.1.3

milan (minikube, network testbench-milan 10.250.2.0/24)
  NODE       ROLES          TIER      IP
  milan      control-plane  worker-0  10.250.2.2
  milan-m02  worker         worker-1  10.250.2.3
```

## Configuration

```yaml
default: # optional: fields copied into every cluster that does not set them
  runtime: k3d

clusters:
  - name: rome
    runtime: kind # required (here or in `default`)
    cni: flannel # default flannel
    nodes: 2 # default 1
    namespaces: # optional workloads
      - name: demo
        manifests: [app.yaml]
        deployments: [...]
        services: [...]
        pods: [...]

network: # optional: addresses and connectivity of the node networks
  cidr: 10.250.0.0/16 # default
  links: # default: every cluster reaches every other one
    - [rome, milan]

tools: # optional
  - registry_proxy
```

The whole configuration, including the Kubernetes specs of the workloads, is
validated before any cluster is touched. Full reference:
[docs/configuration.md](docs/configuration.md).

## Available components

| Runtime    | Description                                                                               |
| ---------- | ----------------------------------------------------------------------------------------- |
| `kind`     | Clusters of docker containers ([kind](https://kind.sigs.k8s.io/)).                        |
| `k3d`      | k3s clusters of docker containers ([k3d](https://k3d.io/)), with `LoadBalancer` services. |
| `minikube` | VMs (`kvm2` driver) or docker containers ([minikube](https://minikube.sigs.k8s.io/)).     |

| CNI       | Description                                                             | Requirements |
| --------- | ----------------------------------------------------------------------- | ------------ |
| `flannel` | Default. Backends `vxlan`, `host-gw`, `wireguard`.                      | -            |
| `calico`  | Through the Tigera operator; `Iptables`, `Nftables` or `BPF` dataplane. | -            |
| `cilium`  | Optional kube-proxy replacement.                                        | `cilium` CLI |

| Tool             | Description                                                                                                                                                                                                                                                                                              | Requirements |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| `registry_proxy` | Caching proxy for container images, shared by the clusters: faster recreations. Recommended to avoid the Docker Hub pull rate limit, but **not secure**: the nodes trust its CA (it intercepts their TLS traffic). It runs on the infrastructure network, reachable only from the host and the clusters. | -            |
| `liqo`           | Installs [Liqo](https://liqo.io) on the selected clusters, peers them and offloads namespaces.                                                                                                                                                                                                           | `liqoctl`    |

## Networks

Every cluster has a network of its own, whatever its runtime, with a /24 of
a single address pool (`network.cidr`, default `10.250.0.0/16`):

| Network               | Subnet                            | Type    | Used by                                        |
| --------------------- | --------------------------------- | ------- | ---------------------------------------------- |
| `testbench_infra`     | 1st /24 (`10.250.0.0/24`)         | docker  | registry proxy (created only if used)          |
| `testbench-<cluster>` | next /24s, in configuration order | docker  | kind, k3d and minikube (`docker` driver) nodes |
| `testbench-<cluster>` | same                              | libvirt | minikube (`kvm2` driver) VMs                   |

The networks are created by the testbench before the clusters (an existing
network with a different configuration is recreated). The pool must not
overlap the `cluster_cidr` and `service_cidr` of the clusters, nor other
networks of the host.

**Routing.** The host is the gateway of every network and routes between
them **without NAT**: the nodes see the real source address of the nodes of
the other clusters. `network.links` lists the pairs of clusters that reach
each other (both directions); omitted, every cluster reaches every other
one. Every cluster reaches the infrastructure network. Any other traffic
between the testbench networks is dropped. Traffic towards the Internet is
masqueraded as usual.

```yaml
network:
  links: # rome <-> milan and milan <-> turin, rome and turin isolated
    - [rome, milan]
    - [milan, turin]
```

The routing is implemented with iptables rules in chains owned by the
testbench, rebuilt at every run (`sudo` asks the password once, at the
start). They are needed, and applied, when there is more than one network
(two clusters, or a cluster and the registry proxy) or a libvirt network:

| Chain                       | Jumped from                              | Content                                                                                                                                                 |
| --------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `filter TESTBENCH-FORWARD`  | `DOCKER-USER` (`FORWARD` without Docker) | Accepts the traffic within a network (bridged), between linked clusters and with the infrastructure network; drops the rest between testbench networks. |
| `nat TESTBENCH-POSTROUTING` | `POSTROUTING`                            | No masquerading between testbench networks.                                                                                                             |
| `filter TESTBENCH-INPUT`    | `INPUT`                                  | DHCP and DNS of the libvirt networks (dnsmasq on the host).                                                                                             |

The docker networks use `com.docker.network.bridge.gateway_mode_ipv4=nat-unprotected`
(Docker ≥ 28), so that Docker does not drop the packets routed to the
containers from the other networks. The libvirt networks use the `open`
forward mode (libvirt adds no NAT and no firewall rule): the minikube VMs
reach the Internet through their interface on the libvirt `default`
network, and the other testbench networks through the host (route pushed by
DHCP and set on every VM).

The rules are not persistent: they are lost at reboot and re-applied by the
next run. `down` removes them; to remove them by hand:

```bash
sudo iptables -D DOCKER-USER -j TESTBENCH-FORWARD   # or -D FORWARD without Docker
sudo iptables -t nat -D POSTROUTING -j TESTBENCH-POSTROUTING
sudo iptables -D INPUT -j TESTBENCH-INPUT
sudo iptables -F TESTBENCH-FORWARD && sudo iptables -X TESTBENCH-FORWARD
sudo iptables -t nat -F TESTBENCH-POSTROUTING && sudo iptables -t nat -X TESTBENCH-POSTROUTING
sudo iptables -F TESTBENCH-INPUT && sudo iptables -X TESTBENCH-INPUT
```

Other notes:

- **k3d** clusters expose `LoadBalancer` services (k3s ServiceLB) on the
  node addresses, reachable from the linked clusters.
- Pod and service CIDRs (`cluster_cidr`, `service_cidr`) are per cluster;
  peered clusters do not need different ones, but distinct CIDRs avoid the
  Liqo address remapping.

## Compatibility

Developed on Linux (Ubuntu) with Docker 29 and the iptables backend.

- **Docker firewall backend.** When at least one cluster runs on docker
  (kind, k3d, minikube `docker` driver), the testbench requires the
  **iptables** firewall backend of Docker (the default; `iptables+firewalld`
  is accepted) and stops with an error otherwise. With the nftables backend
  (`"firewall-backend": "nftables"` in `/etc/docker/daemon.json`, Docker ≥ 29) Docker does not create the `DOCKER-USER` chain, and its nftables rules
  would drop the traffic routed between the networks whatever the testbench
  rules.
- **Fedora (firewalld).** On Fedora (and derivatives) the testbench prints a
  warning at the start. firewalld filters the forwarded traffic in its own
  nftables table, independently of the iptables rules: an `ACCEPT` of the
  testbench does not override a firewalld `DROP`, so the traffic between the
  networks may be dropped (e.g. between the docker bridges, in the `docker`
  zone, and the libvirt bridges, in the `libvirt` zone). If the clusters
  cannot reach each other, allow the forwarding between the zones, e.g. by
  moving the testbench bridges (`br-*` of the `testbench*` docker networks,
  `tb-*` of the libvirt networks) to the `trusted` zone:
  `sudo firewall-cmd --zone=trusted --add-interface=<bridge>`.
- **Other host firewalls.** ufw and similar tools usually only filter the
  `INPUT` chain: the DHCP and DNS of the libvirt networks are allowed by the
  testbench rules. Firewalls that rebuild the iptables rules (e.g. a
  firewall restart) remove the testbench rules: re-run the testbench.
- **IPv4 forwarding** is enabled by the testbench if disabled
  (`net.ipv4.ip_forward=1`).
- **Docker < 28** has no `nat-unprotected` gateway mode: the networks use
  the default one, which on these versions does not block the routed
  traffic.
- **minikube `kvm2` and Liqo.** The kernel of the minikube ISO is built
  without WireGuard (`CONFIG_WIREGUARD` not set, minikube v1.39), so the
  Liqo gateways cannot create their kernel tunnel. On these clusters the
  testbench installs Liqo with the userspace WireGuard implementation
  (`networking.gatewayTemplates.wireguard.implementation=userspace`), which
  works but has a lower throughput: keep it in mind for network
  measurements. Other runtimes (and minikube `docker`) use the host kernel.
- **macOS / Windows** are not supported: Docker Desktop runs the containers
  in a VM, whose networks the host cannot route.

## Examples

| File                                                       | Content                                                                                                           |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| [examples/minimal.yaml](examples/minimal.yaml)             | One kind cluster, all defaults.                                                                                   |
| [examples/workloads.yaml](examples/workloads.yaml)         | One k3d cluster with Calico, a deployment, a service, a pod and a [YAML manifest](examples/manifests/hello.yaml). |
| [examples/liqo_k3d.yaml](examples/liqo_k3d.yaml)           | Two k3d clusters peered with Liqo, a pod offloaded to the provider, registry proxy.                               |
| [examples/liqo_minikube.yaml](examples/liqo_minikube.yaml) | Two minikube VMs with Cilium, peered with Liqo.                                                                   |

## Documentation

- [docs/configuration.md](docs/configuration.md): every configuration field,
  runtime, CNI and tool parameter.
- [docs/development.md](docs/development.md): architecture, execution flow,
  tool hooks, how to add a runtime, CNI or tool.
- [TODO.md](TODO.md): known limitations and planned work.

## License

[Apache License 2.0](LICENSE).
