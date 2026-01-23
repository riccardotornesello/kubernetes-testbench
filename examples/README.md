# Example Configurations

This directory contains example configuration files demonstrating different use cases for Kubernetes Testbench.

## Available Examples

### 1. `base.yaml` - Basic Two-Cluster Setup
A simple configuration with two clusters and Liqo peering. Perfect for getting started with multi-cluster testing.

**Features:**
- 2 clusters (rome, milan)
- k3d runtime
- Calico CNI
- 2 nodes per cluster
- Liqo installation and peering

**Usage:**
```bash
python main.py  # Uses examples/base.yaml by default
```

### 2. `single-cluster.yaml` - Minimal Configuration
The simplest possible setup with a single cluster. Great for learning or basic testing.

**Features:**
- 1 cluster
- k3d runtime
- Calico CNI
- 1 node

**Usage:**
```bash
# Modify main.py to use this config, or copy its contents to base.yaml
```

### 3. `multi-cni.yaml` - Multiple CNI Comparison
Creates three clusters, each with a different CNI plugin. Useful for comparing networking solutions or testing CNI-specific features.

**Features:**
- 3 clusters with different CNIs:
  - calico-cluster: Calico CNI, 2 nodes
  - cilium-cluster: Cilium CNI, 2 nodes
  - flannel-cluster: Flannel CNI, 1 node
- Different network CIDRs to avoid conflicts

**Usage:**
```bash
# Modify main.py to use this config
```

### 4. `advanced.yaml` - Complex Multi-Cluster Setup
Demonstrates advanced features including default values inheritance and multi-cluster Liqo topology.

**Features:**
- Default values for common settings
- 3 clusters (production, staging, development)
- Mixed CNIs (Calico and Cilium)
- Variable node counts
- Liqo installation across all clusters
- Multiple peering relationships

**Usage:**
```bash
# Modify main.py to use this config
```

## Customizing Examples

### Changing the Configuration File

The tool currently uses `examples/base.yaml` by default. To use a different configuration:

1. **Option 1**: Replace the contents of `base.yaml` with your desired configuration
2. **Option 2**: Modify `main.py` line 69 to point to a different file:
   ```python
   cfg = validate_config_file("examples/your-config.yaml")
   ```

### Creating Your Own Configuration

Use these templates as starting points and customize:

1. **Cluster Names**: Change to meaningful names for your use case
2. **Node Count**: Adjust based on your testing needs and system resources
3. **CNI Plugin**: Choose based on features you need to test
4. **Network CIDRs**: Ensure non-overlapping ranges if you have specific requirements
5. **Tools**: Add or remove tool configurations as needed

## Configuration Tips

### Resource Considerations

Each cluster node runs as a Docker container. Consider your system resources:

- **Minimal**: 1-2 clusters with 1 node each
- **Light**: 2-3 clusters with 2 nodes each  
- **Heavy**: 3+ clusters with 3+ nodes each (requires 16GB+ RAM)

### Network CIDR Selection

Default CIDR ranges work well, but you may need to customize if:
- You have existing networks that conflict
- You need specific IP ranges for testing
- You're connecting to external clusters

Ensure:
- Pod CIDRs don't overlap between clusters
- Service CIDRs don't overlap between clusters
- CIDRs don't conflict with your host network

### CNI Selection Guide

- **Calico**: Full-featured, well-tested, great for learning
- **Cilium**: Advanced features, eBPF-based, excellent observability
- **Flannel**: Lightweight, k3d default, minimal features

## Troubleshooting

### Example Won't Run

1. Check Docker is running: `docker ps`
2. Verify prerequisites are installed (k3d, kubectl, etc.)
3. Check for port conflicts if clusters won't start
4. Review configuration syntax using the validator

### Clusters Take Too Long

- Reduce the number of nodes
- Use Flannel CNI (faster than Calico/Cilium)
- Disable tools temporarily
- Check system resources aren't exhausted

## Next Steps

After exploring examples:

1. Read the main [README.md](../README.md) for full documentation
2. Review the configuration reference for all available options
3. Explore the source code to understand implementation details
4. Create custom configurations for your specific testing needs
