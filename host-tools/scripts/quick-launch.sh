#!/bin/bash
# DEPRECATED compatibility shim — quick-launch.sh is now `chutes-cvm launch`.
#
# The launch orchestrator was ported from this script into Python
# (chutes_cvm.guest.launch). This shim is kept only so existing miner automation that
# invokes quick-launch.sh by path (e.g. a systemd unit's ExecStart) keeps working across
# the upgrade. It forwards every argument verbatim to the CLI.
#
# Please update your automation to call `chutes-cvm launch` directly; this shim may be
# removed in a future release.
set -euo pipefail

echo "quick-launch.sh is deprecated — forwarding to 'chutes-cvm launch'. Update your" >&2
echo "automation (e.g. systemd ExecStart) to call 'chutes-cvm launch' directly." >&2

if ! command -v chutes-cvm >/dev/null 2>&1; then
  echo "Error: 'chutes-cvm' not found on PATH. Install it via src/chutes-cvm/install.sh." >&2
  exit 1
fi

exec chutes-cvm launch "$@"
