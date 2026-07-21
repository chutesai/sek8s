#!/usr/bin/env bash
# compute-rtmr1-2.sh — Compute expected RTMR1/RTMR2 (direct boot) from a qcow2 at build time.
#
# Extracts the kernel/initrd/cmdline from the (pre-encryption) image and runs the
# virtee/tdx-measure fork in --runtime-only direct-boot mode. Direct boot's
# RTMR1/RTMR2 depend only on kernel/initrd/cmdline (no shim/grub/MOK), and
# --runtime-only assumes >2.75 GB guest RAM, so no memory/topology input is needed
# — the values are version-level (identical across GPU topologies).
#
# Mirrors compute-rtmr3.sh: a version-level artifact computed before LUKS
# encryption, when the image is still plaintext. Pairs with the launcher's
# direct-boot change: the cmdline here must equal the launcher's -append (both are
# the grub default entry minus the BOOT_IMAGE= prefix, via extract-vm-measurements.sh).
#
# Output:
#   <image>.rtmr1, <image>.rtmr2   (bare uppercase hex, matching <image>.rtmr3)
#
# Usage: compute-rtmr1-2.sh <path-to-qcow2>
# Env:   TDX_MEASURE_BIN  path to the tdx-measure binary (default: tdx-measure on PATH)
#
# Prerequisites on the build host: guestfish (libguestfs-tools) and the tdx-measure
# fork binary (virtee/tdx-measure, chutesai fork).

set -euo pipefail

IMG="${1:-}"
TDX_MEASURE_BIN="${TDX_MEASURE_BIN:-tdx-measure}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ -n "$IMG" ] || { echo "Usage: $0 <path-to-qcow2>" >&2; exit 1; }
[ -f "$IMG" ] || { echo "ERROR: image not found: $IMG" >&2; exit 1; }
if ! command -v "$TDX_MEASURE_BIN" >/dev/null 2>&1 && [ ! -x "$TDX_MEASURE_BIN" ]; then
    echo "ERROR: tdx-measure not found (set TDX_MEASURE_BIN). Build the virtee/tdx-measure fork." >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Extract kernel/initrd/cmdline (direct-boot inputs). The shared extractor strips
# the BOOT_IMAGE= prefix, yielding the -append cmdline the launcher will pass.
( cd "$WORK" && bash "$SCRIPT_DIR/extract-vm-measurements.sh" "$IMG" )
BOOT="$WORK/measure/boot"
KERNEL="$BOOT/vmlinuz"
INITRD="$BOOT/initrd.img"
CMDLINE="$(cat "$BOOT/cmdline.txt")"

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

OUT1="${IMG%.*}.rtmr1"
OUT2="${IMG%.*}.rtmr2"
printf '%s\n' "$RTMR1" > "$OUT1"
printf '%s\n' "$RTMR2" > "$OUT2"

echo >&2
echo "==> Written: $OUT1  ($RTMR1)" >&2
echo "==> Written: $OUT2  ($RTMR2)" >&2
