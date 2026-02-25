#!/bin/bash
# setup-hugepages.sh — idempotent hugepage reservation for TDX VM
#
# Reserves hugepages in the kernel sysfs pool.
# Used by memory-backend-memfd (hugetlb=on,hugetlbsize=1G) which draws
# directly from the kernel hugepage pool via memfd_create() — no hugetlbfs
# mount required.
#
# Safe to call repeatedly; exits 0 immediately if already configured.
#
# Usage:
#   ./setup-hugepages.sh [--size SIZE] [--page-size 1G|2M]
#
# Defaults: --size 100G --page-size 1G

set -e

SIZE="100G"
PAGE_SIZE="1G"

while [[ $# -gt 0 ]]; do
  case $1 in
    --size) SIZE="$2"; shift 2 ;;
    --page-size) PAGE_SIZE="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Parse size to bytes, then to page count
size_to_bytes() {
  local s="${1^^}"
  local num="${s%[KMGTP]B}"
  num="${num%[KMGT]}"
  local unit="${s: -1}"
  # Strip trailing B if present (e.g. GB -> G)
  [[ "${s: -2}" == "GB" || "${s: -2}" == "MB" ]] && unit="${s: -2:1}"
  case "$unit" in
    G) echo $(( num * 1024 * 1024 * 1024 )) ;;
    M) echo $(( num * 1024 * 1024 )) ;;
    K) echo $(( num * 1024 )) ;;
    *) echo $(( num )) ;;
  esac
}

SIZE_BYTES=$(size_to_bytes "$SIZE")

if [[ "$PAGE_SIZE" == "1G" ]]; then
  PAGE_BYTES=$(( 1024 * 1024 * 1024 ))
  HUGEPAGES_DIR="/sys/kernel/mm/hugepages/hugepages-1048576kB"
  PAGES_NEEDED=$(( (SIZE_BYTES + PAGE_BYTES - 1) / PAGE_BYTES ))
elif [[ "$PAGE_SIZE" == "2M" ]]; then
  PAGE_BYTES=$(( 2 * 1024 * 1024 ))
  HUGEPAGES_DIR="/sys/kernel/mm/hugepages/hugepages-2048kB"
  PAGES_NEEDED=$(( (SIZE_BYTES + PAGE_BYTES - 1) / PAGE_BYTES ))
else
  echo "Error: --page-size must be 1G or 2M (got: $PAGE_SIZE)"
  exit 1
fi

# Check if 1G hugepages are supported by this CPU
if [[ "$PAGE_SIZE" == "1G" && ! -d "$HUGEPAGES_DIR" ]]; then
  echo "✗ Error: 1G hugepages not supported by this kernel/CPU."
  echo "  Check: grep pdpe1gb /proc/cpuinfo"
  echo "  Retry with: --page-size 2M"
  exit 1
fi

# Read current reservation
CURRENT_PAGES=$(cat "$HUGEPAGES_DIR/nr_hugepages" 2>/dev/null || echo 0)

if [[ "$CURRENT_PAGES" -ge "$PAGES_NEEDED" ]]; then
  echo "✓ Hugepages already configured: ${CURRENT_PAGES} x ${PAGE_SIZE} pages (need ${PAGES_NEEDED})"
  exit 0
fi

echo "Reserving hugepages: ${PAGES_NEEDED} x ${PAGE_SIZE} pages for ${SIZE} VM..."

# Reserve hugepages (requires root)
if [[ "$EUID" -ne 0 ]]; then
  echo "✗ Error: hugepage reservation requires root. Re-run with sudo."
  exit 1
fi

echo "$PAGES_NEEDED" > "$HUGEPAGES_DIR/nr_hugepages"

# Verify allocation succeeded
ACTUAL_PAGES=$(cat "$HUGEPAGES_DIR/nr_hugepages")
if [[ "$ACTUAL_PAGES" -lt "$PAGES_NEEDED" ]]; then
  echo "✗ Error: Only ${ACTUAL_PAGES} x ${PAGE_SIZE} hugepages allocated (need ${PAGES_NEEDED})."
  echo "  The system may lack sufficient contiguous memory."
  echo "  Allocate hugepages at boot via kernel cmdline for guaranteed availability:"
  echo "    hugepagesz=1G hugepages=${PAGES_NEEDED}"
  exit 1
fi

echo "✓ Reserved ${ACTUAL_PAGES} x ${PAGE_SIZE} hugepages"
echo "✓ Hugepage setup complete"
