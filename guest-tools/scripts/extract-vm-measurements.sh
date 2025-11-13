#!/usr/bin/env bash
set -euo pipefail

#
# extract-vm-measurements.sh
#
# Extracts:
#   - kernel (vmlinuz)
#   - initramfs (initrd.img)
#   - kernel cmdline
#
# from a qcow2 image whose root filesystem is encrypted with LUKS.
#

IMG="${1:-}"
OUT_DIR="measure/boot"

if [[ -z "$IMG" ]]; then
  echo "Usage: $0 <path-to-qcow2>"
  exit 1
fi

if [[ ! -f "$IMG" ]]; then
  echo "ERROR: Image not found: $IMG"
  exit 1
fi

mkdir -p "$OUT_DIR"

echo "=== TDX Boot Artifact Extraction ==="
echo "Image: $IMG"
echo

echo "==> Detecting LUKS partition..."
LUKS_PART=$(guestfish --ro -a "$IMG" <<EOF
run
list-filesystems
EOF
 | awk '/crypto_LUKS/ {print $1}')

if [[ -z "$LUKS_PART" ]]; then
  echo "ERROR: No LUKS partition found in qcow2."
  exit 1
fi

echo "Found LUKS partition: $LUKS_PART"
echo

echo "==> Unlocking LUKS container"
echo "NOTE: You will be prompted for the LUKS passphrase."
echo

guestfish --ro -a "$IMG" <<EOF
run

luks-open $LUKS_PART cryptroot

# Now detect the decrypted filesystem
fs=\$(list-filesystems | awk '/cryptroot/ {print \$1}')

if [ -z "\$fs" ]; then
  echo "ERROR: Decrypted filesystem not found."
  exit 1
fi

mount \$fs /

# List detected boot files
echo "Boot contents:"
ls /boot

# Extract kernel (matches vmlinuz or vmlinuz-*)
download /boot/vmlinuz \$OUT_DIR/vmlinuz || \
download /boot/vmlinuz-* \$OUT_DIR/vmlinuz

# Extract initrd (matches initrd.img or initrd.img-*)
download /boot/initrd.img \$OUT_DIR/initrd.img || \
download /boot/initrd.img-* \$OUT_DIR/initrd.img

# Extract GRUB config so we can parse cmdline outside guestfish
download /boot/grub/grub.cfg \$OUT_DIR/grub.cfg

EOF

echo "✓ Extracted kernel → $OUT_DIR/vmlinuz"
echo "✓ Extracted initrd → $OUT_DIR/initrd.img"
echo "✓ Extracted grub.cfg → $OUT_DIR/grub.cfg"
echo

echo "==> Parsing kernel command line..."
CMDLINE=$(grep -E "^[[:space:]]*linux" "$OUT_DIR/grub.cfg" \
  | head -n 1 \
  | sed -E 's/^[[:space:]]*linux[[:space:]]+[^[:space:]]+[[:space:]]+//' \
  || true)

if [[ -z "$CMDLINE" ]]; then
  echo "ERROR: Could not parse cmdline from grub.cfg"
  exit 1
fi

echo "$CMDLINE" > "$OUT_DIR/cmdline.txt"
echo "✓ Extracted cmdline → $OUT_DIR/cmdline.txt"
echo

echo "=== Extraction Complete ==="
echo "Artifacts written to: $OUT_DIR"
echo
echo "  - $OUT_DIR/vmlinuz"
echo "  - $OUT_DIR/initrd.img"
echo "  - $OUT_DIR/cmdline.txt"
echo
echo "These are now ready for ACPI extraction + tdx-measure."
