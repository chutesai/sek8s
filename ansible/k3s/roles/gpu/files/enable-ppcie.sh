#!/bin/bash
set -e

# Check if GPUs are already in ReadyState=1
if nvidia-smi conf-compute -q | grep -q "CC GPUs Ready State *: *Ready"; then
    echo "GPUs already in ReadyState=1"
    exit 0
fi

# Enable PPCIe mode (all GPUs)
echo "Enabling PPCIe ReadyState=1..."
nvidia-smi conf-compute -srs 1

# Verify
if nvidia-smi conf-compute -q | grep -q "CC GPUs Ready State *: *Ready"; then
    echo "PPCIe GPUs enabled successfully"
else
    echo "Failed to enable PPCIe GPUs"
    exit 1
fi