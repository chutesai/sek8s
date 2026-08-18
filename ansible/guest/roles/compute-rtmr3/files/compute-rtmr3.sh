#!/usr/bin/env bash
# compute-rtmr3.sh — Compute the expected RTMR3 from a qcow2 image at build time.
#
# Mounts the image read-only via guestmount and simulates the exact SHA-384
# extension chain that the rtmr3-measure initramfs init-bottom script runs at
# boot, using the same /etc/tdx-measure.conf baked into the image.
#
# Algorithm (identical to rtmr3-verify and rtmr3-measure — must stay in sync):
#   rtmr3 = 0x00...00  (48 zero bytes, TDX initial state)
#   for each regular file F in sorted(root-relative paths from tdx-measure.conf):
#       rtmr3 = SHA384(rtmr3 || SHA384(F.contents))
#
# The sort key is the root-relative path (e.g. /etc/ssh/sshd_config), which is
# what both the shell sort(1) and Python sorted() produce on the live system.
# Stripping the mount prefix before sorting is required for correctness.
#
# Output:
#   Progress and per-file hashes → stderr
#   Bare RTMR3 hex               → stdout  (captured to <image>.rtmr3)
#
# Usage:
#   ./compute-rtmr3.sh <path-to-qcow2> [root-partition]
#
#   root-partition defaults to auto-detect (first ext4 partition found).
#   Override if your image layout has the root on a specific device, e.g.:
#     ./compute-rtmr3.sh guest.qcow2 /dev/sda2
#
# Prerequisites: guestmount (libguestfs-tools), python3
#   sudo apt install libguestfs-tools

set -euo pipefail

IMG="${1:-}"
ROOT_PART_OVERRIDE="${2:-}"

if [[ -z "$IMG" ]]; then
    echo "Usage: $0 <path-to-qcow2> [root-partition]" >&2
    exit 1
fi

if [[ ! -f "$IMG" ]]; then
    echo "ERROR: Image not found: $IMG" >&2
    exit 1
fi

# Output file sits next to the image: <name>.rtmr3
# e.g. /data/prod/guest.qcow2 → /data/prod/guest.rtmr3
OUT_FILE="${IMG%.*}.rtmr3"

if ! command -v guestmount &>/dev/null; then
    echo "ERROR: guestmount not found. Install with: sudo apt install libguestfs-tools" >&2
    exit 1
fi

# ── Auto-detect root partition ────────────────────────────────────────────────

if [[ -n "$ROOT_PART_OVERRIDE" ]]; then
    ROOT_PART="$ROOT_PART_OVERRIDE"
else
    echo "==> Auto-detecting root partition (ext4) ..." >&2
    ROOT_PART=$(
        guestfish --ro -a "$IMG" <<'EOF' | awk '/ext4/ {sub(/:$/, "", $1); print $1; exit}'
run
list-filesystems
EOF
    )
    if [[ -z "$ROOT_PART" ]]; then
        echo "ERROR: Could not find an ext4 root partition. Pass it explicitly as \$2." >&2
        exit 1
    fi
    echo "  Found: $ROOT_PART" >&2
fi

# ── Mount image ───────────────────────────────────────────────────────────────

MNT=$(mktemp -d --suffix=-rtmr3-compute)
cleanup() {
    if mountpoint -q "$MNT" 2>/dev/null; then
        guestunmount "$MNT" 2>/dev/null || fusermount -u "$MNT" 2>/dev/null || true
    fi
    rmdir "$MNT" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Mounting $IMG ($ROOT_PART) at $MNT ..." >&2
guestmount --ro -a "$IMG" -m "$ROOT_PART" "$MNT"

CONF="$MNT/etc/tdx-measure.conf"
if [[ ! -f "$CONF" ]]; then
    echo "ERROR: /etc/tdx-measure.conf not found in image — rtmr3-measure role may not have run." >&2
    exit 1
fi

echo "==> Computing RTMR3 from /etc/tdx-measure.conf ..." >&2
echo >&2

# ── Compute extension chain ───────────────────────────────────────────────────
#
# Python writes progress (per-file hashes, summary) to stderr.
# The bare RTMR3 hex is written to stdout so the shell can capture it cleanly.

RTMR3_HEX=$(python3 - "$MNT" "$CONF" <<'PYEOF'
import hashlib
import sys
from pathlib import Path

mount_root = sys.argv[1].rstrip("/")
conf_path  = sys.argv[2]

# Parse conf — strip comments and blank lines (same logic as rtmr3-verify)
cfg_paths: list[str] = []
for line in Path(conf_path).read_text().splitlines():
    line = line.split("#", 1)[0].strip()
    if line:
        cfg_paths.append(line)

if not cfg_paths:
    print("ERROR: no paths configured in tdx-measure.conf", file=sys.stderr)
    sys.exit(1)

# Collect regular files, recording their ROOT-RELATIVE path for sorting.
# Mirrors rtmr3-measure (shell): find -type f, strip rootmnt prefix, sort.
# Mirrors rtmr3-verify (python): rglob + is_file() + not is_symlink() + sorted().
# Sort key must be the root-relative path so ordering matches the live system.
entries: list[tuple[str, str]] = []  # (root_relative_path, full_mounted_path)

for cfg_path in cfg_paths:
    mounted = Path(mount_root + cfg_path)
    if mounted.is_dir():
        for f in mounted.rglob("*"):
            if f.is_file() and not f.is_symlink():
                rel = str(f)[len(mount_root):]
                entries.append((rel, str(f)))
    elif mounted.is_file() and not mounted.is_symlink():
        entries.append((cfg_path, str(mounted)))

entries.sort(key=lambda e: e[0])

if not entries:
    print("ERROR: no files found to measure — check tdx-measure.conf paths", file=sys.stderr)
    sys.exit(1)

print(f"  Measuring {len(entries)} files:", file=sys.stderr)

# Extension chain (identical to rtmr3-verify.compute_expected_rtmr3):
#   rtmr3_{i+1} = SHA384(rtmr3_i || SHA384(file_contents))
rtmr3 = bytes(48)  # TDX initial RTMR3 = 48 zero bytes

for rel_path, full_path in entries:
    file_hash = hashlib.sha384(Path(full_path).read_bytes()).digest()
    rtmr3 = hashlib.sha384(rtmr3 + file_hash).digest()
    print(f"  {file_hash.hex()}  {rel_path}", file=sys.stderr)

hex_val = rtmr3.hex().upper()
print(f"\nRTMR3: {hex_val}", file=sys.stderr)

# Bare hex to stdout for shell capture
print(hex_val)
PYEOF
)

# ── Emit to stdout (captured into an Ansible fact; no on-disk artifact) ────────

printf '%s\n' "$RTMR3_HEX"
