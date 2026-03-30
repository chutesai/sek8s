#!/bin/bash
# reset-gpus.sh - Reset all GPUs via nvidia-gpu-tools Secondary Bus Reset.
#
# Ensures no VM is running before resetting to prevent corrupting active
# workloads. The VM must be stopped gracefully before running this.
#
# Usage:
#   sudo ./devices/reset-gpus.sh
#   chutes-reset-gpus              # via PATH (after host setup)

set -euo pipefail

PROCESS_NAME="chutes-td"

usage() {
    cat <<EOF
Usage: $(basename "$0") [--help]

Reset all NVIDIA GPUs via Secondary Bus Reset (SBR) using nvidia-gpu-tools.

The VM process ($PROCESS_NAME) must not be running during reset.
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
        ;;
    *)
        echo "Unknown option: $1"
        usage
        exit 1
        ;;
esac

if pgrep -f "$PROCESS_NAME" > /dev/null 2>&1; then
    echo "Error: VM process '$PROCESS_NAME' is running."
    echo ""
    echo "Stop the VM gracefully before resetting GPUs:"
    echo "  chutes-miner tee shutdown --ip <HOST_IP> --confirm"
    echo "  chutes-miner tee shutdown --name <SERVER_NAME> --confirm"
    exit 1
fi

CMD=$(which nvidia-gpu-tools 2>/dev/null || echo "")
if [[ -z "$CMD" ]]; then
    echo "Error: nvidia-gpu-tools not found in PATH."
    echo "It is installed automatically when run-td launches a VM,"
    echo "or install manually from host-tools/scripts/gpu-tools/."
    exit 1
fi

echo "Resetting GPUs via Secondary Bus Reset..."
sudo "$CMD" --reset-with-sbr --reset-after-ppcie-mode-switch
echo "GPU reset complete."
