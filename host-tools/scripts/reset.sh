#!/usr/bin/env bash
# reset-all-nvidia.sh — reset every NVIDIA GPU via PCI function-level reset
#
# Run as root:  sudo ./reset-all-nvidia.sh
# Use case: Reset stuck GPUs that are passed through to VMs

set -euo pipefail

need_root() { [[ $(id -u) -eq 0 ]] || { echo "Run as root." >&2; exit 1; }; }

reset_device() {
    local dev="$1"
    local reset_path="/sys/bus/pci/devices/$dev/reset"
    
    if [[ ! -e "$reset_path" ]]; then
        echo "  ✗ $dev: no reset method available" >&2
        return 1
    fi
    
    echo "  → Resetting $dev"
    if echo 1 > "$reset_path" 2>/dev/null; then
        echo "  ✓ $dev reset successful"
        return 0
    else
        echo "  ✗ $dev reset failed" >&2
        return 1
    fi
}

reset_group() {
    local dev="$1" grp dir
    dir=$(readlink -f "/sys/bus/pci/devices/$dev/iommu_group") || {
        echo "✗ $dev: no IOMMU group" >&2; return; }
    grp=$(basename "$dir")
    
    echo "▶ Resetting IOMMU group $grp (triggered by $dev)"
    
    for node in "$dir"/devices/*; do
        fn=$(basename "$node")
        class=$(cat "/sys/bus/pci/devices/$fn/class" 2>/dev/null || echo "")
        
        # Skip bridges
        if [[ $class == "0x060400" ]]; then
            echo "  • $fn is a PCIe bridge (skipping)"
            continue
        fi
        
        reset_device "$fn"
    done
    echo
}

###############################################################################
# main
###############################################################################
need_root

# Detect every NVIDIA GPU (class