#!/bin/bash
# prepare-vm-image.sh - Verify base image SHA256 and create/reuse per-VM image copy
# Usage: VM_IMAGE=$(./prepare-vm-image.sh "$BASE_IMAGE" "$HOSTNAME" "$EXPECTED_BASE_SHA256" "$VM_IMAGE_DIR" [skip_checksum])
# Exits 1 on verification failure; prints VM image path on success.
# Optional 5th arg: "1", "true", or "yes" to skip checksum verification (for debug with custom images)
#
# The per-VM image is a full copy of the base image (not a qcow2 overlay).
# This means luksRemoveKey destroys the old key slot in-place on the only copy,
# matching the security model of the storage and cache volumes.
# Stale per-VM images from a previous base image version are removed on upgrade.

set -e

BASE_IMAGE="$1"
HOSTNAME="$2"
EXPECTED_SHA="$3"
VM_IMAGE_DIR="$4"
SKIP_VERIFY="${5:-}"

[[ ! -f "$BASE_IMAGE" ]] && { echo "ERROR: base image not found: $BASE_IMAGE" >&2; exit 1; }
[[ "$BASE_IMAGE" != *.qcow2 ]] && { echo "ERROR: base image must be qcow2 (got: $BASE_IMAGE)" >&2; exit 1; }
[[ -z "$VM_IMAGE_DIR" ]] && { echo "ERROR: VM image directory not provided" >&2; exit 1; }

ACTUAL_SHA=$(sha256sum "$BASE_IMAGE" | awk '{print $1}')

if [[ "$SKIP_VERIFY" == "1" || "$SKIP_VERIFY" == "true" || "$SKIP_VERIFY" == "yes" ]]; then
  echo "Skipping base image checksum verification (debug mode)" >&2
  SHA_FOR_IMAGE="$ACTUAL_SHA"
else
  [[ -z "$EXPECTED_SHA" ]] && { echo "ERROR: expected SHA256 not provided (use --skip-checksum for debug)" >&2; exit 1; }
  if [[ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]]; then
    echo "ERROR: base image hash mismatch" >&2
    echo "  This quick-launch expects: $EXPECTED_SHA" >&2
    echo "  Actual base image hash:    $ACTUAL_SHA" >&2
    echo "  Run: quick-launch.sh --download  (to fetch the correct VM from https://vm.chutes.ai)" >&2
    echo "  Or use --skip-checksum for debug with a custom image" >&2
    exit 1
  fi
  echo "Verified base image: $BASE_IMAGE (sha256=$ACTUAL_SHA)" >&2
  SHA_FOR_IMAGE="$EXPECTED_SHA"
fi

[[ -d "$VM_IMAGE_DIR" ]] || sudo mkdir -p "$VM_IMAGE_DIR"

VM_IMAGE="${VM_IMAGE_DIR}/tdx-${HOSTNAME}-${SHA_FOR_IMAGE:0:16}.qcow2"

# Remove stale per-VM images from previous base image versions.
for stale in "${VM_IMAGE_DIR}"/tdx-"${HOSTNAME}"-*.qcow2; do
  [[ -f "$stale" ]] || continue
  [[ "$stale" == "$VM_IMAGE" ]] && continue
  echo "Removing stale VM image: $stale" >&2
  rm -f "$stale"
done

if [[ -f "$VM_IMAGE" ]]; then
  echo "Using existing VM image: $VM_IMAGE" >&2
else
  echo "Copying base image to per-VM image: $VM_IMAGE" >&2
  if ! cp "$BASE_IMAGE" "$VM_IMAGE"; then
    echo "ERROR: failed to copy base image to per-VM image" >&2
    exit 1
  fi
fi

echo "$VM_IMAGE"
