#!/usr/bin/env bash
set -euo pipefail

# ----------------------------------------------------------
# Configuration
# ----------------------------------------------------------

IMG="${1:-}"
OUT_DIR="measure"
BOOT_DIR="$OUT_DIR/boot"

if [[ -z "$IMG" ]]; then
  echo "Usage: $0 <path-to-qcow2>"
  exit 1
fi

if [[ ! -f "$IMG" ]]; then
  echo "Error: qcow2 image not found: $IMG"
  exit 1
fi

mkdir -p "$OUT_DIR"
mkdir -p "$BOOT_DIR"

echo "=== Extracting VM boot artifacts for TDX measurement ==="
echo "Image: $IMG"
echo

# ----------------------------------------------------------
# Use guestfish to identify kernel + initrd automatically
# ----------------------------------------------------------

echo "• Detecting kernel and initrd inside qcow2..."

KERNEL_PATH=$(guestfish --ro -a "$IMG" -i ls /boot | grep '^vmlinuz' | head -n 1 || true)
INITRD_PATH=$(guestfish --ro -a "$IMG" -i ls /boot | grep -E '^initrd.*img' | head -n 1 || true)

if [[ -z "$KERNEL_PATH" ]]; then
  echo "Error: Could not find vmlinuz inside /boot of qcow2 image."
  exit 1
fi

if [[ -z "$INITRD_PATH" ]]; then
  echo "Error: Could not find initrd.img inside /boot of qcow2 image."
  exit 1
fi

echo "  Found kernel: /boot/$KERNEL_PATH"
echo "  Found initrd: /boot/$INITRD_PATH"
echo

# ----------------------------------------------------------
# Extract kernel
# ----------------------------------------------------------

echo "• Extracting kernel..."
guestfish --ro -a "$IMG" -i cat "/boot/$KERNEL_PATH" > "$BOOT_DIR/vmlinuz"
echo "  → $BOOT_DIR/vmlinuz"
echo

# ----------------------------------------------------------
# Extract initrd
# ----------------------------------------------------------

echo "• Extracting initramfs..."
guestfish --ro -a "$IMG" -i cat "/boot/$INITRD_PATH" > "$BOOT_DIR/initrd.img"
echo "  → $BOOT_DIR/initrd.img"
echo

# ----------------------------------------------------------
# Extract kernel command line from GRUB config
# ----------------------------------------------------------

echo "• Extracting kernel command line from grub.cfg..."

GRUB_CFG=$(guestfish --ro -a "$IMG" -i cat /boot/grub/grub.cfg)

CMDLINE=$(echo "$GRUB_CFG" | \
  grep -E "^[[:space:]]*linux" | \
  head -n 1 | \
  sed -E 's/^[[:space:]]*linux[[:space:]]+[^[:space:]]+[[:space:]]+//' || true)

if [[ -z "$CMDLINE" ]]; then
  echo "Error: Unable to extract kernel command line from /boot/grub/grub.cfg"
  exit 1
fi

echo "$CMDLINE" > "$BOOT_DIR/cmdline.txt"
echo "  → $BOOT_DIR/cmdline.txt"
echo

# ----------------------------------------------------------
# Summary
# ----------------------------------------------------------

echo "=== Extraction Complete ==="
echo "Artifacts written to: $BOOT_DIR"
echo
echo "You now have:"
echo "  $BOOT_DIR/vmlinuz"
echo "  $BOOT_DIR/initrd.img"
echo "  $BOOT_DIR/cmdline.txt"
echo
echo "Use these in your metadata.json for tdx-measure."
echo
