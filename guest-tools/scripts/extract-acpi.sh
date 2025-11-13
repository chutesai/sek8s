#!/usr/bin/env bash
set -euo pipefail

# ----------------------------------------------------------
# Paths produced by extract-vm-measurements.sh
# ----------------------------------------------------------

TDVF="firmware/TDVF.fd"              # You committed this manually
KERNEL="measure/boot/vmlinuz"        # Extracted kernel
INITRD="measure/boot/initrd.img"     # Extracted initramfs
CMDLINE_FILE="measure/boot/cmdline.txt"  # Extracted cmdline
OUT_DIR="measure/acpi"

MEM="4G"    # Enough for ACPI init; does not affect measurements

mkdir -p "$OUT_DIR"

echo "=== Extracting ACPI tables for TDX measurement ==="
echo "Using:"
echo "  TDVF:    $TDVF"
echo "  Kernel:  $KERNEL"
echo "  Initrd:  $INITRD"
echo "  Cmdline: $(cat $CMDLINE_FILE)"
echo

# Load kernel command line
CMDLINE=$(cat "$CMDLINE_FILE")

# ----------------------------------------------------------
# Launch QEMU long enough to emit ACPI tables, then exit
# ----------------------------------------------------------

/usr/bin/qemu-system-x86_64 \
  -accel kvm \
  -object memory-backend-memfd,id=ram0,size=$MEM \
  -machine q35,kernel-irqchip=split,confidential-guest-support=tdx,memory-backend=ram0,dumpdtb=$OUT_DIR/acpi-tables.dtb \
  -bios "$TDVF" \
  -kernel "$KERNEL" \
  -initrd "$INITRD" \
  -append "$CMDLINE" \
  -S \
  -no-reboot \
  -display none \
  -serial none \
  -monitor none \
  -nographic

echo "✓ ACPI dump complete: $OUT_DIR/acpi-tables.dtb"
echo
echo "You can now reference this file in metadata.json via:"
echo '  "acpi_dtb": "acpi/acpi-tables.dtb"'
