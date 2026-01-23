# Kubernetes Testbench

A flexible and extensible tool for creating and managing virtual Kubernetes clusters locally for testing and development purposes. Originally designed for [Liqo](https://liqo.io/) testing but adaptable to any Kubernetes testing scenario.

## Features

- 🚀 **Quick Cluster Creation**: Spin up multiple Kubernetes clusters with a single command
- 🔌 **Multiple CNI Support**: Choose from Calico, Cilium, or Flannel for your cluster networking
- 🎯 **Extensible Architecture**: Easily add support for new cluster runtimes, CNIs, and tools
- 🔗 **Multi-Cluster Setup**: Create complex multi-cluster environments with ease
- 🛠️ **Tool Integration**: Built-in support for Liqo and extensible for other tools
- ⚙️ **YAML Configuration**: Simple, declarative configuration format

## Prerequisites

Before using Kubernetes Testbench, ensure you have the following tools installed:

- **Python 3.12+**: The tool is written in Python and requires version 3.12 or higher
- **Docker**: Required for running k3d clusters
- **k3d**: Kubernetes cluster manager for Docker (https://k3d.io)
- **kubectl**: Kubernetes command-line tool
- **Cilium CLI** (optional): Required if using Cilium CNI (https://cilium.io)
- **Liqoctl** (optional): Required if using Liqo tool integration (https://liqo.io)

## Installation

1. Clone the repository:
```bash
git clone https://github.com/riccardotornesello/kubernetes-testbench.git
cd kubernetes-testbench
```

2. Install dependencies using `uv` (recommended) or `pip`:
```bash
# Using uv (faster)
uv sync

# Or using pip
pip install -r pyproject.toml
```

## Quick Start

1. Create a configuration file (see `examples/base.yaml` for reference):
```yaml
clusters:
  - name: "cluster1"
    runtime: "k3d"
    cni: "calico"
    nodes: 2
  - name: "cluster2"
    runtime: "k3d"
    cni: "calico"
    nodes: 2
```

2. Run the tool:
```bash
python main.py
```

The tool will create the clusters, install the selected CNI, and apply any configured tools.

## Configuration Reference

### Configuration File Structure

```yaml
# Optional: Default values inherited by all clusters
default:
  runtime: "k3d"           # Cluster runtime
  cni: "calico"            # CNI plugin
  nodes: 1                 # Number of nodes
  cluster_cidr: "10.200.0.0/16"  # Pod network CIDR
  service_cidr: "10.71.0.0/16"   # Service network CIDR

# Required: List of clusters to create
clusters:
  - name: "cluster-name"   # Required: Unique cluster name
    runtime: "k3d"         # Optional: Overrides default
    cni: "calico"          # Optional: Overrides default
    nodes: 2               # Optional: Overrides default
    cluster_cidr: "..."    # Optional: Overrides default
    service_cidr: "..."    # Optional: Overrides default

# Optional: Tool configurations
tools:
  liqo:
    installations:
      - cluster: "cluster-name"
        version: "latest"  # Optional: Specific version
    peerings:
      - ["cluster1", "cluster2"]
```

### Supported Options

#### Runtimes
- `k3d`: Lightweight Kubernetes distribution in Docker

#### CNI Plugins
- `calico`: Project Calico (default: v3.30.3)
- `cilium`: Cilium (default: v1.18.6)
- `flannel`: Flannel (k3d default, no installation needed)

#### Tools
- `liqo`: Multi-cluster networking and resource sharing

## Examples

See the [examples/](examples/) directory for sample configurations:

- `base.yaml`: Basic two-cluster setup with Liqo peering
- Additional examples coming soon

## Architecture

### Project Structure

```
kubernetes-testbench/
├── main.py              # Entry point
├── config.py            # Configuration validation and parsing
├── const.py             # Global constants
├── logs.py              # Logging utilities
├── clusters/            # Cluster runtime implementations
│   ├── base.py         # Abstract base class for clusters
│   └── k3d.py          # k3d cluster implementation
├── cni/                 # CNI plugin implementations
│   ├── base.py         # Abstract base class for CNIs
│   ├── calico.py       # Calico CNI implementation
│   └── cilium.py       # Cilium CNI implementation
├── tools/               # Tool integrations
│   ├── base.py         # Abstract base class for tools
│   └── liqo.py         # Liqo tool implementation
└── examples/            # Example configurations
```

### How It Works

1. **Configuration Loading**: Reads and validates YAML configuration file
2. **Network Setup**: Creates a Docker network for cluster communication
3. **Cluster Creation**: Instantiates and creates each configured cluster
4. **CNI Installation**: Installs the selected CNI plugin in each cluster
5. **Tool Installation**: Installs and configures any specified tools
6. **Kubeconfig Generation**: Saves kubeconfig files to `out/kubeconfigs/`

### Extending the Tool

#### Adding a New Cluster Runtime

1. Create a new class in `clusters/` that extends `Cluster`
2. Implement the `init_cluster()` and `install_cni()` methods
3. Add the new runtime to `RuntimeEnum` in `config.py`
4. Update the `parse()` function in `main.py` to handle the new runtime

#### Adding a New CNI

1. Create a new class in `cni/` that extends `CNI`
2. Implement the `install()` method
3. Add the new CNI to `CNIEnum` in `config.py`
4. Update cluster implementations to handle the new CNI

#### Adding a New Tool

1. Create a new class in `tools/` that extends `Tool`
2. Implement the `install()` method
3. Add configuration models in `config.py`
4. Update `main.py` to instantiate and install the new tool

## Troubleshooting

### Common Issues

**Issue**: Cluster creation fails with Docker network error
- **Solution**: Ensure Docker is running and you have permissions to create networks

**Issue**: CNI installation timeout
- **Solution**: Increase wait time or check cluster connectivity

**Issue**: Liqo peering fails
- **Solution**: Verify both clusters are accessible and Liqo is properly installed

**Issue**: Kubeconfig not found
- **Solution**: Check that clusters were created successfully and `out/kubeconfigs/` directory exists

### Debug Mode

For verbose output, modify the configuration or check cluster logs:
```bash
k3d cluster list
kubectl --kubeconfig out/kubeconfigs/<cluster-name>.yaml get nodes
```

## Contributing

Contributions are welcome! Please feel free to submit issues, feature requests, or pull requests.

### Development Setup

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run linting: `uv run ruff check .`
5. Submit a pull request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built for [Liqo](https://liqo.io/) multi-cluster testing
- Uses [k3d](https://k3d.io/) for lightweight Kubernetes clusters
- Supports [Calico](https://www.tigera.io/project-calico/) and [Cilium](https://cilium.io/) CNI plugins
