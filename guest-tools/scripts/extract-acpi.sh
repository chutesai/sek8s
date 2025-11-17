#!/usr/bin/env bash
set -euo pipefail

TDVF="firmware/TDVF.fd"
OUT_DIR="measure/acpi"
MEM="2G"

mkdir -p "$OUT_DIR"

echo "=== Extracting ACPI tables (TDVF only) ==="
echo "TDVF: $TDVF"
echo

# Optional: sanity check
if [[ ! -s "$TDVF" ]]; then
  echo "ERROR: TDVF not found or empty at $TDVF"
  exit 1
fi

timeout 20 qemu-system-x86_64 \
  -machine "q35,dumpdtb=$OUT_DIR/acpi-tables.dtb" \
  -accel tcg \
  -m "$MEM" \
  -bios "$TDVF" \
  -display none \
  -nographic \
  -serial none \
  -monitor none \
  -no-reboot

if [[ ! -s "$OUT_DIR/acpi-tables.dtb" ]]; then
  echo "ERROR: ACPI dump failed or produced empty file: $OUT_DIR/acpi-tables.dtb"
  exit 1
fi

echo "✓ ACPI dump complete: $OUT_DIR/acpi-tables.dtb"
