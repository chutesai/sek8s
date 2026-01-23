#!/bin/bash
# Bundle GPU admin tools from NVIDIA gpu-admin-tools repository
# This script clones the repo, creates a wheel package, and installs it locally

set -e

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
TARGET_DIR="${SCRIPT_DIR}"
GPU_ADMIN_TOOLS_URL="https://github.com/NVIDIA/gpu-admin-tools.git"
BUILD_DIR="${TARGET_DIR}/.build"

echo "Bundling GPU admin tools from NVIDIA gpu-admin-tools repository..."
echo "Repository: ${GPU_ADMIN_TOOLS_URL}"
echo "Target: ${TARGET_DIR}"
echo ""

# Clean up any previous build
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

echo "Cloning gpu-admin-tools repository..."
git clone --depth 1 "${GPU_ADMIN_TOOLS_URL}" "${BUILD_DIR}/gpu-admin-tools" 2>&1 | grep -v "^Cloning\|^remote:\|^Resolving\|^Receiving\|^Updating" || true

REPO_DIR="${BUILD_DIR}/gpu-admin-tools"

if [ ! -f "${REPO_DIR}/nvidia_gpu_tools.py" ]; then
    echo "Error: Could not find nvidia_gpu_tools.py in gpu-admin-tools repository"
    exit 1
fi

echo "Copying repository files to build directory..."
BUILD_SRC_DIR="${BUILD_DIR}/source"
mkdir -p "${BUILD_SRC_DIR}"

# Copy the main script
cp "${REPO_DIR}/nvidia_gpu_tools.py" "${BUILD_SRC_DIR}/"

# Copy required module directories
for dir in utils gpu pci cli; do
    if [ -d "${REPO_DIR}/${dir}" ]; then
        echo "  Copying ${dir}/ directory..."
        cp -r "${REPO_DIR}/${dir}" "${BUILD_SRC_DIR}/"
    fi
done

# Check for setup.py or pyproject.toml in the repo
if [ -f "${REPO_DIR}/setup.py" ]; then
    cp "${REPO_DIR}/setup.py" "${BUILD_SRC_DIR}/"
elif [ -f "${REPO_DIR}/pyproject.toml" ]; then
    cp "${REPO_DIR}/pyproject.toml" "${BUILD_SRC_DIR}/"
else
    # Create a minimal setup.py if neither exists
    echo "  Creating setup.py..."
    cat > "${BUILD_SRC_DIR}/setup.py" << 'SETUP_EOF'
from setuptools import setup, find_packages

setup(
    name="nvidia-gpu-admin-tools",
    version="2025.11.21",
    description="NVIDIA GPU Admin Tools for Confidential Computing configuration",
    long_description="Tools for configuring GPU modes (CC mode vs PPCIe mode) for GPU passthrough in TDX VMs",
    author="NVIDIA",
    url="https://github.com/NVIDIA/gpu-admin-tools",
    license="MIT",
    py_modules=['nvidia_gpu_tools'],
    packages=find_packages(exclude=['tests', '*.tests', '*.tests.*']),
    python_requires=">=3.8",
    entry_points={
        'console_scripts': [
            'nvidia-gpu-tools=nvidia_gpu_tools:main',
        ],
    },
)
SETUP_EOF
fi

echo ""
echo "Building wheel package..."
cd "${BUILD_SRC_DIR}"

# Build the wheel
if command -v python3 &> /dev/null; then
    python3 -m pip install --upgrade pip build wheel 2>&1 | grep -v "^Requirement\|^Collecting\|^Using\|^Already" || true
    python3 -m build --wheel 2>&1 | grep -v "^Creating\|^Adding\|^Copying\|^Building" || true
    
    # Find the built wheel and move it to target directory
    WHEEL_FILE=$(find dist -name "*.whl" 2>/dev/null | head -1)
    if [ -n "${WHEEL_FILE}" ]; then
        WHEEL_NAME=$(basename "${WHEEL_FILE}")
        # Remove any existing wheel files
        rm -f "${TARGET_DIR}"/*.whl
        mv "${WHEEL_FILE}" "${TARGET_DIR}/${WHEEL_NAME}"
        echo ""
        echo "✓ Successfully built wheel package"
        echo "  Location: ${TARGET_DIR}/${WHEEL_NAME}"
        echo ""
        echo "The wheel file is ready to be committed to the repository."
        echo "The run-td script will automatically install it if nvidia-gpu-tools is not in PATH."
    else
        echo "Error: Could not find built wheel file"
        exit 1
    fi
else
    echo "Error: python3 not found, cannot build wheel"
    exit 1
fi

# Clean up build directory and any leftover source files
rm -rf "${BUILD_DIR}"
# Remove any source files that might have been left behind
rm -rf "${TARGET_DIR}"/utils "${TARGET_DIR}"/gpu "${TARGET_DIR}"/pci "${TARGET_DIR}"/cli
rm -f "${TARGET_DIR}"/nvidia_gpu_tools.py "${TARGET_DIR}"/setup.py "${TARGET_DIR}"/pyproject.toml
rm -rf "${TARGET_DIR}"/build "${TARGET_DIR}"/dist "${TARGET_DIR}"/*.egg-info

echo ""
echo "✓ GPU admin tools bundled successfully"
echo "  Wheel file: ${TARGET_DIR}/${WHEEL_NAME}"
echo ""
echo "Only the wheel file should be committed to the repository."
