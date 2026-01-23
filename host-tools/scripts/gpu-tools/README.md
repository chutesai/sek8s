# Bundled GPU Admin Tools

This directory contains a bundled wheel package of NVIDIA's GPU admin tools.

## Wheel Package

The wheel file (`nvidia_gpu_admin_tools-*.whl`) is a pre-built Python package that can be installed on the host system. This tool is used to configure GPU modes (CC mode vs PPCIe mode) for GPU passthrough in TDX VMs.

### Source

The wheel is built from:
- Repository: https://github.com/NVIDIA/gpu-admin-tools
- Built using: `poetry build --format wheel` or `python3 -m build --wheel`

### Building the Wheel

To rebuild the wheel package (for maintainers):

```bash
cd host-tools/scripts/gpu-tools
./bundle-tools.sh
```

This script will:
1. Clone the gpu-admin-tools repository
2. Create a pyproject.toml with the correct entry point
3. Build a wheel package
4. Place the wheel file in this directory
5. Clean up all source files (only the wheel remains)

**Note:** Only the `.whl` file should be committed to the repository. Source files are ignored via `.gitignore`.

### Usage

The `run-td` script automatically handles installation:

1. **Checks for installed package** - If `nvidia-gpu-tools` command is in PATH, uses it
2. **Installs from bundled wheel** - If not installed, automatically installs from the wheel file in this directory into a venv and creates a system-wide symlink

Users don't need to manually install anything - the `run-td` script handles it automatically.

### License

This tool is part of NVIDIA's gpu-admin-tools repository. Please refer to the repository for license information.
