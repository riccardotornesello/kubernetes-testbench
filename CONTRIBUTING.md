# Contributing to Kubernetes Testbench

Thank you for your interest in contributing to Kubernetes Testbench! This document provides guidelines and instructions for contributing.

## Code of Conduct

Please be respectful and considerate in your interactions with other contributors and maintainers.

## How to Contribute

### Reporting Bugs

If you find a bug, please open an issue with:
- A clear, descriptive title
- Steps to reproduce the issue
- Expected behavior vs actual behavior
- Your environment (OS, Python version, Docker version, etc.)
- Any relevant logs or error messages

### Suggesting Enhancements

Enhancement suggestions are welcome! Please open an issue with:
- A clear description of the feature
- Use cases and benefits
- Any implementation ideas you have

### Pull Requests

1. **Fork the repository** and create a new branch from `main`
2. **Make your changes** following the code style guidelines
3. **Test your changes** thoroughly
4. **Run the linter**: `python -m ruff check .`
5. **Commit your changes** with clear, descriptive commit messages
6. **Push to your fork** and open a pull request

#### Pull Request Guidelines

- Keep changes focused - one feature or fix per PR
- Update documentation if you're changing functionality
- Add examples if you're adding new features
- Reference related issues in your PR description

## Development Setup

### Prerequisites

- Python 3.12+
- Docker
- k3d
- kubectl

### Installation

1. Clone the repository:
```bash
git clone https://github.com/riccardotornesello/kubernetes-testbench.git
cd kubernetes-testbench
```

2. Install dependencies:
```bash
pip install -e .
pip install ruff  # For linting
```

### Running Tests

Currently, the project focuses on manual testing. To test your changes:

1. Create a test configuration in `examples/`
2. Run the tool: `python main.py`
3. Verify cluster creation and tool installation
4. Clean up: `k3d cluster delete <cluster-names>`

## Code Style

### Python Style

- Follow PEP 8 guidelines
- Use type hints for function parameters and return values
- Maximum line length: 88 characters
- Use meaningful variable and function names
- Add docstrings to all modules, classes, and public functions

### Docstring Format

Use Google-style docstrings:

```python
def function_name(param1: str, param2: int) -> bool:
    """
    Short description of the function.
    
    Longer description if needed, explaining the function's purpose,
    behavior, and any important details.
    
    Args:
        param1: Description of param1
        param2: Description of param2
        
    Returns:
        Description of return value
        
    Raises:
        ValueError: Description of when this is raised
    """
```

### Linting

Before committing, always run:
```bash
python -m ruff check .
```

Fix any issues reported by the linter.

## Project Structure

```
kubernetes-testbench/
├── main.py              # Entry point
├── config.py            # Configuration validation
├── const.py             # Global constants
├── logs.py              # Logging utilities
├── clusters/            # Cluster implementations
│   ├── base.py         # Abstract base class
│   └── k3d.py          # k3d implementation
├── cni/                 # CNI plugin implementations
│   ├── base.py         # Abstract base class
│   ├── calico.py       # Calico CNI
│   └── cilium.py       # Cilium CNI
├── tools/               # Tool integrations
│   ├── base.py         # Abstract base class
│   └── liqo.py         # Liqo integration
└── examples/            # Example configurations
```

## Adding New Features

### Adding a New Cluster Runtime

1. Create a new file in `clusters/` (e.g., `kind.py`)
2. Extend the `Cluster` base class
3. Implement `init_cluster()` and `install_cni()` methods
4. Add the runtime to `RuntimeEnum` in `config.py`
5. Update `parse()` in `main.py` to handle the new runtime
6. Add documentation and examples

### Adding a New CNI Plugin

1. Create a new file in `cni/` (e.g., `weave.py`)
2. Extend the `CNI` base class
3. Implement the `install()` method
4. Add the CNI to `CNIEnum` in `config.py`
5. Update cluster implementations to handle the new CNI
6. Add documentation and examples

### Adding a New Tool

1. Create a new file in `tools/` (e.g., `istio.py`)
2. Extend the `Tool` base class
3. Implement the `install()` method
4. Add configuration models in `config.py`
5. Update `main.py` to handle the new tool
6. Add documentation and examples

## Documentation

When adding new features:
- Update the main README.md
- Add or update examples in `examples/`
- Add inline code comments for complex logic
- Update docstrings if changing function signatures

## Questions?

If you have questions about contributing, feel free to open an issue labeled "question".

## License

By contributing to Kubernetes Testbench, you agree that your contributions will be licensed under the same license as the project.
