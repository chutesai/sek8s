#!/bin/bash
# prepare-vm-image.sh - Instantiate the per-VM copy of a published image SET.
# Usage: VM_IMAGE=$(./prepare-vm-image.sh "$BASE_IMAGE_SET_DIR" "$HOSTNAME" "$VM_IMAGE_DIR")
# Exits 1 on verification failure; prints the per-VM image path on success.
#
# $BASE_IMAGE_SET_DIR is a published image-set DIRECTORY — the qcow2 plus its
# .vmlinuz/.initrd/.cmdline and a manifest.json. There is exactly one image format: the
# set. chutes_cvm.guest.image_set verifies the set is coherent (all files present, sizes match
# the manifest) and returns the qcow2 path + its manifest-recorded sha256, so we neither
# re-hash a multi-GB image on every launch nor rely on a pinned expected-hash constant.
#
# The per-VM image is a full copy of the base qcow2 (not a qcow2 overlay). luksRemoveKey
# destroys the old key slot in-place on the only copy, matching the security model of the
# storage and cache volumes. Stale per-VM images from a previous base version are removed.

set -e

BASE_IMAGE="$1"
HOSTNAME="$2"
VM_IMAGE_DIR="$3"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

[[ -z "$BASE_IMAGE" ]] && { echo "ERROR: base image set not provided" >&2; exit 1; }
[[ -z "$VM_IMAGE_DIR" ]] && { echo "ERROR: VM image directory not provided" >&2; exit 1; }
[[ -d "$BASE_IMAGE" ]] || {
  echo "ERROR: base image must be a published image-set directory (got: $BASE_IMAGE)." >&2
  echo "  Stage it with 'chutes-cvm image download' or via ansible before launching." >&2
  exit 1
}

# Verify the set against its manifest; get back the qcow2 path + its manifest sha256.
RESOLVE_OUT=$(PYTHONPATH="$SCRIPT_DIR/../../src/chutes-cvm" python3 -m chutes_cvm.guest.image_set verify "$BASE_IMAGE") || exit 1
eval "$RESOLVE_OUT"   # sets QCOW2 and SHA256
BASE_IMAGE="$QCOW2"
SHA_FOR_IMAGE="$SHA256"
echo "Verified image set via manifest: $BASE_IMAGE (sha256=$SHA_FOR_IMAGE)" >&2

[[ -d "$VM_IMAGE_DIR" ]] || sudo mkdir -p "$VM_IMAGE_DIR"

VM_IMAGE="${VM_IMAGE_DIR}/tdx-${HOSTNAME}-${SHA_FOR_IMAGE:0:16}.qcow2"

# Remove stale per-VM images (and their direct-boot sidecars) from previous base versions.
for stale in "${VM_IMAGE_DIR}"/tdx-"${HOSTNAME}"-*.qcow2; do
  [[ -f "$stale" ]] || continue
  [[ "$stale" == "$VM_IMAGE" ]] && continue
  echo "Removing stale VM image: $stale" >&2
  rm -f "$stale" "${stale%.qcow2}".vmlinuz "${stale%.qcow2}".initrd "${stale%.qcow2}".cmdline
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

# Stage the direct-boot sidecars (1.4.0+) next to the per-VM image. The launcher resolves
# <image-base>.{vmlinuz,initrd,cmdline} next to the *per-VM* copy it boots, so they must
# travel with the copy — not just live next to the base image. Copy unconditionally so a
# reused per-VM image also re-syncs. Missing base sidecars are fatal: without them chutes-cvm guest launch
# cannot direct-boot.
BASE_BASE="${BASE_IMAGE%.qcow2}"
VM_BASE="${VM_IMAGE%.qcow2}"
for ext in vmlinuz initrd cmdline; do
  src="${BASE_BASE}.${ext}"
  if [[ ! -f "$src" ]]; then
    echo "ERROR: direct-boot artifact missing next to base image: $src" >&2
    echo "  The image must ship with .vmlinuz/.initrd/.cmdline (built by the" >&2
    echo "  stage-boot-artifacts step, published to R2 alongside the qcow2)." >&2
    exit 1
  fi
  cp "$src" "${VM_BASE}.${ext}"
done

echo "$VM_IMAGE"
