#!/usr/bin/env bash
# publish-base-image.sh — Upload a built base image + its sidecars to R2.
#
# The base image is the Ubuntu cloud image plus the `common` package layer, built by
# ansible/guest/playbooks/base-image.yml. The guest build consumes it by URL and validates it
# against ansible/guest/BASE_IMAGE_SHA256, so what lands here must be exactly the bytes that
# file pins:
#   guest-tools/image/base/<ver>/base-<ver>.qcow2{,.sha256,.provenance}
#     -> <bucket>/base-images/<ver>/base-<ver>.qcow2{,.sha256,.provenance}
#
# Published versions are immutable. A guest build pins a version AND its hash, so replacing the
# bytes under an existing version breaks every consumer that already pinned it — the download
# succeeds and the checksum rejects it. Cut a new version instead; --force exists only for
# re-uploading an interrupted transfer of the same bytes.
#
# The sidecars go last so the published .sha256 never advertises a partial image. They are for
# third parties reproducing the layer; our own builds trust the in-repo pin, not the sidecar.
#
# rclone remote "r2" must be configured. If the rclone config is password protected, export
# RCLONE_CONFIG_PASS (otherwise rclone prompts on each call).
#
# Usage: publish-base-image.sh [--version <ver>] [--bucket r2:chutes-tdx] [--force]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/ansible/guest/BASE_IMAGE_VERSION")"
BUCKET="r2:chutes-tdx"
FORCE=false

while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --bucket) BUCKET="$2"; shift 2 ;;
        --force) FORCE=true; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

command -v rclone >/dev/null 2>&1 || { echo "ERROR: rclone not found" >&2; exit 1; }

# Check the remote resolves before hashing 7GB and starting an upload. The Makefile prompts for the
# config password unconditionally, so without this a missing config looks like a password prompt
# that "worked" and then fails much later with rclone's opaque "didn't find section in config file".
if ! rclone listremotes 2>/dev/null | grep -q "^${BUCKET%%:*}:"; then
    echo "ERROR: rclone remote '${BUCKET%%:*}:' is not configured for $(whoami) on $(hostname)." >&2
    echo "  rclone is reading: $(rclone config file 2>&1 | tail -1)" >&2
    echo "  Publish from a host that has the remote, or configure it here (rclone config)." >&2
    exit 1
fi
[ -n "$VERSION" ] || { echo "ERROR: no base image version (ansible/guest/BASE_IMAGE_VERSION is empty)" >&2; exit 1; }

BASE_DIR="$REPO_ROOT/guest-tools/image/base/$VERSION"
IMG="$BASE_DIR/base-$VERSION.qcow2"
REMOTE_DIR="$BUCKET/base-images/$VERSION"

[ -f "$IMG" ] || {
    echo "ERROR: no built base image at $IMG" >&2
    echo "  (run: ansible-playbook -i <inventory> playbooks/base-image.yml)" >&2
    exit 1
}

# The pin is the contract. Publishing bytes nobody pinned means a guest build downloads an image
# its own checksum rejects — a confusing mid-build failure rather than a clear one here.
PINNED="$(tr -d '[:space:]' < "$REPO_ROOT/ansible/guest/BASE_IMAGE_SHA256" 2>/dev/null || true)"
ACTUAL="$(sha256sum "$IMG" | cut -d' ' -f1)"
if [ "$PINNED" != "$ACTUAL" ]; then
    echo "ERROR: BASE_IMAGE_SHA256 does not match the built image:" >&2
    echo "  pinned: ${PINNED:-(empty)}" >&2
    echo "  actual: $ACTUAL" >&2
    echo "Update ansible/guest/BASE_IMAGE_SHA256 first." >&2
    exit 1
fi

# Test the OUTPUT, not the exit code: rclone lsf exits 0 for a missing object and simply prints
# nothing, so an exit-code check reports "already published" on every first publish.
EXISTING="$(rclone lsf "$REMOTE_DIR/base-$VERSION.qcow2" 2>/dev/null || true)"
if [ "$FORCE" != true ] && [ -n "$EXISTING" ]; then
    echo "ERROR: $REMOTE_DIR/base-$VERSION.qcow2 already exists." >&2
    echo "Published versions are immutable: consumers pin <version, hash> together, so different" >&2
    echo "bytes under this version would fail their checksum. Bump BASE_IMAGE_VERSION instead." >&2
    echo "(--force only for re-uploading an interrupted transfer of these same bytes.)" >&2
    exit 1
fi

# Buffers are CONCURRENCY x chunk-size in RAM (16 x 64M = 1G); raise only on a host that has it.
UPLOAD_CONCURRENCY="${RCLONE_UPLOAD_CONCURRENCY:-16}"

echo "Publishing base image $VERSION -> $REMOTE_DIR/"
echo "  sha256: $ACTUAL"
rclone copyto --progress --s3-chunk-size 64M --transfers 4 \
    --s3-upload-concurrency "$UPLOAD_CONCURRENCY" "$IMG" "$REMOTE_DIR/base-$VERSION.qcow2"

for ext in sha256 provenance; do
    src="$IMG.$ext"
    if [ -f "$src" ]; then
        echo "==> $src -> $REMOTE_DIR/base-$VERSION.qcow2.$ext"
        rclone copyto "$src" "$REMOTE_DIR/base-$VERSION.qcow2.$ext"
    else
        echo "WARNING: $src missing — publishing without it" >&2
    fi
done

echo "✓ Published base image $VERSION"
