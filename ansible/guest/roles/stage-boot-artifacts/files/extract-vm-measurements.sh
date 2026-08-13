#!/usr/bin/env bash
set -euo pipefail

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

echo "==> Detecting the boot filesystem (carries vmlinuz/initrd/grub)..."
# Match the ext4 partition that actually holds the versioned kernel, not just "any ext4".
# On an encrypted (prod) image the root is LUKS, so only the unencrypted /boot partition
# is ext4 — but on an unencrypted (debug) image BOTH the root and /boot partitions are
# ext4, so a bare /ext4/ match returns two devices and the later mount fails. The kernel
# (vmlinuz-*) lives only on the /boot partition, so pick by that.
ROOT_PART=""
for part in $(
  guestfish --ro -a "$IMG" <<'LIST' | awk '/ext4/ {sub(/:$/, "", $1); print $1}'
run
list-filesystems
LIST
); do
  if guestfish --ro -a "$IMG" <<CHECK 2>/dev/null | grep -q 'vmlinuz-'
run
mount $part /
ls /
CHECK
  then
    ROOT_PART="$part"
    break
  fi
done

if [[ -z "$ROOT_PART" ]]; then
  echo "ERROR: Could not find an ext4 partition containing the kernel (vmlinuz-*)."
  exit 1
fi

echo "Found boot partition: $ROOT_PART"
echo

#
# 1. Extract vmlinuz and initrd.img
#

echo "==> Extracting kernel and initrd..."

guestfish --ro -a "$IMG" <<EOF
run
mount $ROOT_PART /

# Extract vmlinuz symlink if present
if exists /vmlinuz ; then
    download /vmlinuz $OUT_DIR/vmlinuz
else
    # Pick newest vmlinuz-* by version
    newest_vmlinuz=\$(glob ls /vmlinuz-* | sort | tail -n1)
    download \$newest_vmlinuz $OUT_DIR/vmlinuz
fi

# Extract initrd symlink if present
if exists /initrd.img ; then
    download /initrd.img $OUT_DIR/initrd.img
else
    newest_initrd=\$(glob ls /initrd.img-* | sort | tail -n1)
    download \$newest_initrd $OUT_DIR/initrd.img
fi

# Extract grub config for cmdline parsing
download /grub/grub.cfg $OUT_DIR/grub.cfg
EOF

echo "✓ Extracted kernel → $OUT_DIR/vmlinuz"
echo "✓ Extracted initrd → $OUT_DIR/initrd.img"
echo "✓ Extracted grub.cfg → $OUT_DIR/grub.cfg"
echo

#
# 2. Parse kernel cmdline
#

echo "==> Parsing kernel cmdline..."
CMDLINE=$(grep -E "^[[:space:]]*linux" "$OUT_DIR/grub.cfg" \
  | head -n 1 \
  | sed -E 's/^[[:space:]]*linux[[:space:]]+[^[:space:]]+[[:space:]]+//'
)

if [[ -z "$CMDLINE" ]]; then
  echo "ERROR: Could not parse kernel cmdline"
  exit 1
fi

echo "$CMDLINE" > "$OUT_DIR/cmdline.txt"
echo "✓ Extracted cmdline → $OUT_DIR/cmdline.txt"

echo
echo "=== Extraction Complete ==="
