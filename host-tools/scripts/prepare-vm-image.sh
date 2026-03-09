#!/bin/bash
# prepare-vm-image.sh - Verify base image SHA256 and create/reuse qcow2 overlay
# Usage: OVERLAY=$(./prepare-vm-image.sh "$BASE_IMAGE" "$HOSTNAME" "$EXPECTED_BASE_SHA256" "$OVERLAY_DIR")
# Exits 1 on verification failure; prints overlay path on success

set -e

BASE_IMAGE="$1"
HOSTNAME="$2"
EXPECTED_SHA="$3"
OVERLAY_DIR="$4"

[[ ! -f "$BASE_IMAGE" ]] && { echo "ERROR: base image not found: $BASE_IMAGE" >&2; exit 1; }
[[ "$BASE_IMAGE" != *.qcow2 ]] && { echo "ERROR: base image must be qcow2 (got: $BASE_IMAGE)" >&2; exit 1; }
[[ -z "$EXPECTED_SHA" ]] && { echo "ERROR: expected SHA256 not provided" >&2; exit 1; }
[[ -z "$OVERLAY_DIR" ]] && { echo "ERROR: overlay directory not provided" >&2; exit 1; }

ACTUAL_SHA=$(sha256sum "$BASE_IMAGE" | awk '{print $1}')
if [[ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]]; then
  echo "ERROR: base image hash mismatch" >&2
  echo "  This quick-launch expects: $EXPECTED_SHA" >&2
  echo "  Actual base image hash:    $ACTUAL_SHA" >&2
  echo "  Run: quick-launch.sh --download  (to fetch the correct VM from https://vm.chutes.ai)" >&2
  exit 1
fi
echo "Verified base image: $BASE_IMAGE (sha256=$ACTUAL_SHA)" >&2

[[ -d "$OVERLAY_DIR" ]] || sudo mkdir -p "$OVERLAY_DIR"

OVERLAY_IMAGE="${OVERLAY_DIR}/tdx-${HOSTNAME}-${EXPECTED_SHA:0:16}.qcow2"
if [[ -f "$OVERLAY_IMAGE" ]]; then
  echo "Using existing overlay: $OVERLAY_IMAGE" >&2
else
  echo "Creating overlay: $OVERLAY_IMAGE" >&2
  qemu-img create -f qcow2 -b "$BASE_IMAGE" -F qcow2 "$OVERLAY_IMAGE"
fi
echo "$OVERLAY_IMAGE"
