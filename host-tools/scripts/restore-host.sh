#!/usr/bin/env bash
# restore-host.sh -- Restore host CPU settings saved by tune-host.sh.
#
# Thin wrapper around `python3 -m chutes.host.tune restore`. Runs the snapshot
# captured at tune time and clears it. No-op if the host was never tuned.
#
# Requires root (writes to /sys).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec python3 -m chutes.host.tune restore "$@"
