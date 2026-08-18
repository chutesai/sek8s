#!/usr/bin/env bash
# compute-rtmr1-2.sh — Compute expected RTMR1/RTMR2 (direct boot) at build time.
#
# Reads the direct-boot artifacts staged by stage-boot-artifacts.sh
# (<image>.vmlinuz/.initrd/.cmdline) and runs the virtee/tdx-measure fork in
# --runtime-only direct-boot mode. Direct boot's RTMR1/RTMR2 depend only on
# kernel/initrd/cmdline (no shim/grub/MOK), and --runtime-only assumes >2.75 GB
# guest RAM, so no memory/topology input is needed — the values are version-level
# (identical across GPU topologies).
#
# Consuming the SAME staged artifacts the launcher boots guarantees the pinned
# RTMR1/2 match the running VM by construction (not by matching extraction logic).
# Mirrors compute-rtmr3.sh: a version-level artifact computed before encryption.
#
# Output:
#   <image>.rtmr1, <image>.rtmr2   (bare uppercase hex, matching <image>.rtmr3)
#
# Usage: compute-rtmr1-2.sh <path-to-qcow2>   (run stage-boot-artifacts.sh first)
# Env:   TDX_MEASURE_BIN  path to the tdx-measure binary (default: tdx-measure on PATH)
#
# Prerequisites on the build host: the tdx-measure fork binary (chutesai fork).

set -euo pipefail

IMG="${1:-}"
TDX_MEASURE_BIN="${TDX_MEASURE_BIN:-tdx-measure}"

[ -n "$IMG" ] || { echo "Usage: $0 <path-to-qcow2>" >&2; exit 1; }
if ! command -v "$TDX_MEASURE_BIN" >/dev/null 2>&1 && [ ! -x "$TDX_MEASURE_BIN" ]; then
    echo "ERROR: tdx-measure not found (set TDX_MEASURE_BIN). Build the virtee/tdx-measure fork." >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Read the staged direct-boot artifacts (the exact bytes the launcher boots).
BASE="${IMG%.*}"
KERNEL="$BASE.vmlinuz"
INITRD="$BASE.initrd"
CMDLINE_FILE="$BASE.cmdline"
for f in "$KERNEL" "$INITRD" "$CMDLINE_FILE"; do
    [ -f "$f" ] || { echo "ERROR: missing $f — run stage-boot-artifacts.sh first" >&2; exit 1; }
done
CMDLINE="$(cat "$CMDLINE_FILE")"

# Direct-boot metadata: RTMR1/2 only — no ACPI/firmware/memory needed. Emit via
# python for correct JSON escaping of the cmdline.
python3 - "$KERNEL" "$INITRD" "$CMDLINE" > "$WORK/metadata.json" <<'PY'
import json, sys
kernel, initrd, cmdline = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({"direct": {"kernel": kernel, "initrd": initrd, "cmdline": cmdline}}))
PY

echo "==> Computing RTMR1/RTMR2 (direct boot) via tdx-measure ..." >&2
OUT="$("$TDX_MEASURE_BIN" --runtime-only "$WORK/metadata.json")"
echo "$OUT" >&2

RTMR1="$(printf '%s\n' "$OUT" | sed -nE 's/^RTMR1:[[:space:]]*([0-9a-fA-F]+).*/\1/p' | tr 'a-f' 'A-F')"
RTMR2="$(printf '%s\n' "$OUT" | sed -nE 's/^RTMR2:[[:space:]]*([0-9a-fA-F]+).*/\1/p' | tr 'a-f' 'A-F')"

if [ -z "$RTMR1" ] || [ -z "$RTMR2" ]; then
    echo "ERROR: failed to parse RTMR1/RTMR2 from tdx-measure output" >&2
    exit 1
fi

# Emit to stdout for the caller to capture into an Ansible fact — no on-disk
# artifact, so a stale .rtmr1/.rtmr2 can't leak from a previous build.
printf 'RTMR1=%s\nRTMR2=%s\n' "$RTMR1" "$RTMR2"
