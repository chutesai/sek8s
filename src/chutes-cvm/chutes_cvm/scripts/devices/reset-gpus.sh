#!/bin/bash
# reset-gpus.sh - Reset all GPUs via nvidia-gpu-tools Secondary Bus Reset.
#
# Ensures no VM is running before resetting to prevent corrupting active
# workloads. The VM must be stopped gracefully before running this.
#
# Which reset (CC or PPCIe) is the GPU profile's decision, not this script's: run it through
# `chutes-cvm host reset-gpus`, which passes the profile's nvidia-gpu-tools arguments.
#
# Usage:
#   chutes-cvm host reset-gpus
#   sudo ./devices/reset-gpus.sh --reset-with-sbr --reset-after-cc-mode-switch

set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") <nvidia-gpu-tools SBR args...>

Reset all NVIDIA GPUs via Secondary Bus Reset (SBR) using nvidia-gpu-tools.
The SBR arguments come from the host's GPU profile; normally run this through
\`chutes-cvm host reset-gpus\`, which supplies them.

No VM may be running, and no previous one may still be reclaiming its
memory, during reset.
SBR resets clear GPU state and fabric configuration, which would
corrupt any active workloads.

To stop the VM gracefully first:
  chutes-miner tee shutdown --ip <HOST_IP> --confirm
  chutes-miner tee shutdown --name <SERVER_NAME> --confirm
EOF
}

case "${1:-}" in
    --help|-h)
        usage
        exit 0
        ;;
    "")
        echo "Error: no SBR arguments given."
        usage
        exit 1
        ;;
esac
SBR_ARGS=("$@")

# Whether the GPUs are ours to reset is the same device-ownership question a launch asks, so
# ask it the same way rather than re-implementing it here. This script used to match /proc/<pid>/cmdline and
# skip zombies -- both of which miss a QEMU that powered its guest off but is still reclaiming
# the TD's private memory. That process holds every GPU, and SBR-resetting underneath it is the
# worst thing this script can do.
# Fail closed but say so: without the CLI, `if !` below would see exit 127 and refuse with a
# message about the GPUs being held, which would be a lie.
if ! command -v chutes-cvm >/dev/null 2>&1; then
    echo "Error: chutes-cvm not found in PATH; cannot check whether the GPUs are free."
    echo "Run this via \`chutes-cvm host reset-gpus\`, or add the CLI shim to PATH."
    exit 1
fi

if ! chutes-cvm host devices-free; then
    echo ""
    echo "Not resetting: something still holds the GPUs (see above)."
    echo "If a VM is running, stop it gracefully first:"
    echo "  chutes-miner tee shutdown --ip <HOST_IP> --confirm"
    echo "  chutes-miner tee shutdown --name <SERVER_NAME> --confirm"
    exit 1
fi

CMD=$(which nvidia-gpu-tools 2>/dev/null || echo "")
if [[ -z "$CMD" ]]; then
    echo "Error: nvidia-gpu-tools not found in PATH."
    echo "It is installed automatically when chutes-cvm guest launch launches a VM,"
    echo "or install manually from the bundled wheel in chutes_cvm/scripts/gpu-tools/."
    exit 1
fi

# Abort if nvidia-gpu-tools processes are already running (likely stuck in D state).
STUCK_PIDS=$(pgrep -f 'nvidia-gpu-tools' 2>/dev/null | grep -v "^$$\$" || true)
if [[ -n "$STUCK_PIDS" ]]; then
    echo "Error: nvidia-gpu-tools process(es) already running:"
    ps -fp $STUCK_PIDS 2>/dev/null || true
    echo ""
    echo "These are likely stuck in uninterruptible sleep (D state) from a"
    echo "previous reset attempt. A host reboot is required to clear them."
    echo "Running another reset will also hang."
    exit 1
fi

GPU_TOOLS_TIMEOUT=120

echo "Resetting GPUs via Secondary Bus Reset (${SBR_ARGS[*]}, timeout: ${GPU_TOOLS_TIMEOUT}s)..."
if ! timeout "$GPU_TOOLS_TIMEOUT" sudo "$CMD" "${SBR_ARGS[@]}"; then
    echo ""
    echo "Error: GPU reset timed out or failed after ${GPU_TOOLS_TIMEOUT}s."
    echo "GPU hardware may be wedged at the PCIe level."
    echo "A host reboot is likely required to recover."
    exit 1
fi
echo "GPU reset complete."

# PCI remove + rescan: clear stale kernel state (driver bindings, iommufd
# refs, AER errors) so the next VM launch starts with a clean device tree.
NVIDIA_VENDOR="10de"
GPU_BDFS=$(lspci -Dnn | grep "$NVIDIA_VENDOR" | awk '{print $1}')

if [[ -n "$GPU_BDFS" ]]; then
    echo "Removing PCI devices to clear kernel state..."
    for bdf in $GPU_BDFS; do
        remove_path="/sys/bus/pci/devices/${bdf}/remove"
        if [[ -w "$remove_path" ]]; then
            echo "  Removing $bdf"
            echo 1 > "$remove_path"
        fi
    done

    echo "Rescanning PCI bus..."
    echo 1 > /sys/bus/pci/rescan

    # Wait for devices to reappear (up to 5 seconds).
    for _attempt in $(seq 1 10); do
        all_back=true
        for bdf in $GPU_BDFS; do
            if [[ ! -d "/sys/bus/pci/devices/${bdf}" ]]; then
                all_back=false
                break
            fi
        done
        if $all_back; then
            echo "All devices reappeared after rescan."
            break
        fi
        sleep 0.5
    done
fi
