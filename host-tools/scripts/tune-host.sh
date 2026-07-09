#!/usr/bin/env bash
# tune-host.sh -- Apply NVIDIA-recommended host CPU tuning for TDX VMs.
#
# Thin wrapper around `python3 -m chutes.host.tune apply`. Sets the CPU
# governor to performance and disables the C1E/C6 C-states, snapshotting the
# original settings so they can be restored later. Decoupled from VM launch;
# run it deliberately. Revert with restore-host.sh.
#
# Requires root (writes to /sys). Has no effect on TDX measurements.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec python3 -m chutes.host.tune apply "$@"
