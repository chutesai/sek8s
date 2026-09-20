#!/usr/bin/env bash
# publish-image.sh — Upload a built guest image + its direct-boot artifacts to R2.
#
# Uploads the versioned local build outputs to the canonical R2 object names that
# vm.chutes.ai serves and `chutes-cvm image download` fetches. The build writes each set
# into its own directory, guest-tools/image/<env>/<version>[-debug]/:
#   <set>/<version>[-debug].qcow2   -> <bucket>/[<prefix>/]<name>[-debug].qcow2
#   <set>/<version>[-debug].vmlinuz -> <bucket>/[<prefix>/]<name>[-debug].vmlinuz
#   <set>/<version>[-debug].initrd  -> <bucket>/[<prefix>/]<name>[-debug].initrd
#   <set>/<version>[-debug].cmdline -> <bucket>/[<prefix>/]<name>[-debug].cmdline
#   <set>/manifest.json (generated) -> <bucket>/[<prefix>/]<name>[-debug].manifest.json
#
# The .vmlinuz/.initrd/.cmdline are produced by stage-boot-artifacts.sh during the
# build; all four must travel together so the fleet boots byte-identical bits. The
# manifest (sha256 + size per artifact, keyed by role) is the coherence contract that
# ties the set together — `chutes-cvm image download` verifies against it, so a stale or
# mismatched artifact fails loudly instead of as an opaque boot/attestation error.
#
# rclone remote "r2" must be configured. If the rclone config is password
# protected, export RCLONE_CONFIG_PASS (otherwise rclone prompts on each call).
#
# Usage: publish-image.sh [--debug] [--env <env>] [--version <ver>] [--bucket r2:chutes-tdx]
#                         [--prefix <dir>] [--name <basename>] [--force]
#
# --prefix publishes the set into a subdirectory, keeping the basename: `--prefix
# tdx-guest-1.4.0` archives a whole version as one bucket entry rather than five loose
# objects, and the names inside stay canonical, so a restore is a copy back to the root.
# --name overrides the basename instead (`--name 1.4.0` -> 1.4.0.*, flat). Either one makes
# this an archival publish, which refuses to overwrite an existing set unless --force —
# an archive is meant to be immutable. The default target still overwrites, since that is
# the name the fleet serves.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEBUG=false
ENV="prod"
VERSION="$(head -1 "$REPO_ROOT/ansible/guest/VERSION" | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+')"
BUCKET="r2:chutes-tdx"
DEFAULT_NAME="tdx-guest"
NAME="$DEFAULT_NAME"
PREFIX=""
FORCE=false

while [ $# -gt 0 ]; do
    case "$1" in
        --debug) DEBUG=true; shift ;;
        --env) ENV="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --bucket) BUCKET="$2"; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --prefix) PREFIX="${2%/}"; shift 2 ;;
        --force) FORCE=true; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

# Both land in an object key. Validating per segment keeps '..' and empty segments out, so
# the published key is always the one reported below.
SEG='[A-Za-z0-9][A-Za-z0-9._-]*'
if ! printf '%s' "$NAME" | grep -qE "^$SEG\$"; then
    echo "ERROR: --name must be one path segment matching $SEG (got: '$NAME')" >&2
    exit 2
fi
if [ -n "$PREFIX" ] && ! printf '%s' "$PREFIX" | grep -qE "^$SEG(/$SEG)*\$"; then
    echo "ERROR: --prefix must be '/'-separated segments matching $SEG (got: '$PREFIX')" >&2
    exit 2
fi

command -v rclone >/dev/null 2>&1 || { echo "ERROR: rclone not found" >&2; exit 1; }

SUFFIX=""
[ "$DEBUG" = true ] && SUFFIX="-debug"
REMOTE="${NAME}${SUFFIX}"
DEST_DIR="$BUCKET/${PREFIX:+$PREFIX/}"
# This image set's own directory (one per version AND variant, so prod and debug each have
# their own manifest.json), and the shared basename of the four artifacts inside it.
IMAGE_SET_DIR="$REPO_ROOT/guest-tools/image/$ENV/${VERSION}${SUFFIX}"
LOCAL_BASE="$IMAGE_SET_DIR/${VERSION}${SUFFIX}"
ARTIFACTS=(qcow2 vmlinuz initrd cmdline)

# Pre-flight: all four must exist so we never publish a qcow2 without its matching
# boot artifacts (which would leave the fleet unable to direct-boot).
for ext in "${ARTIFACTS[@]}"; do
    src="$LOCAL_BASE.$ext"
    [ -f "$src" ] || {
        echo "ERROR: missing $src" >&2
        echo "  (build the image; stage-boot-artifacts.sh produces the .vmlinuz/.initrd/.cmdline)" >&2
        exit 1
    }
done

# An archive is a backup, so clobbering one silently would destroy the thing it exists to
# preserve. The canonical target is exempt: overwriting it on every release is its job.
if { [ "$NAME" != "$DEFAULT_NAME" ] || [ -n "$PREFIX" ]; } && [ "$FORCE" = false ]; then
    # Fail closed on a listing error: an empty result must mean "nothing there", not
    # "could not ask" (a password-protected rclone config is the usual cause). Exit 3 is
    # rclone's "directory not found" — for the first archive under a new prefix that IS
    # "nothing there", so it is the one non-zero code that must not block.
    existing=""; rc=0
    existing="$(rclone lsf "$DEST_DIR" --include "$REMOTE.*" 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ] && [ "$rc" -ne 3 ]; then
        echo "ERROR: cannot list $DEST_DIR to check for an existing archive (rclone exit $rc):" >&2
        printf '  %s\n' "$existing" >&2
        echo "  (password-protected config? export RCLONE_CONFIG_PASS) or pass --force" >&2
        exit 1
    fi
    [ "$rc" -eq 3 ] && existing=""
    if [ -n "$existing" ]; then
        echo "ERROR: $DEST_DIR$REMOTE.* already exists:" >&2
        printf '  %s\n' $existing >&2
        echo "  pass --force to overwrite, or publish under a different --prefix/--name" >&2
        exit 1
    fi
fi

# Generate the coherence manifest with the single generator (chutes_cvm.guest.image_set), the
# same one the build and the launcher use, so the schema never drifts. It hashes the qcow2
# and its <base>.{vmlinuz,initrd,cmdline} sidecars.
MANIFEST="$IMAGE_SET_DIR/manifest.json"
echo "Generating manifest -> $MANIFEST"
DEBUG_FLAG=()
[ "$DEBUG" = true ] && DEBUG_FLAG=(--debug)
PYTHONPATH="$REPO_ROOT/src/chutes-cvm" python3 -m chutes_cvm.guest.image_set manifest \
    "$LOCAL_BASE.qcow2" -o "$MANIFEST" --version "$VERSION" "${DEBUG_FLAG[@]}"

# --transfers is per-file and all but the qcow2 are tiny; the multi-GB qcow2 is one file, so
# its speed is set by how many 64M parts upload in parallel. rclone's default is 4. Buffers are
# CONCURRENCY x chunk-size in RAM (16 x 64M = 1G), so raise this only on a host that has it.
UPLOAD_CONCURRENCY="${RCLONE_UPLOAD_CONCURRENCY:-16}"

echo "Publishing ${VERSION}${SUFFIX} ($ENV) -> $DEST_DIR$REMOTE.*"
for ext in "${ARTIFACTS[@]}"; do
    src="$LOCAL_BASE.$ext"
    dst="$DEST_DIR$REMOTE.$ext"
    echo "==> $src -> $dst"
    rclone copyto --progress --s3-chunk-size 64M --transfers 4 \
        --s3-upload-concurrency "$UPLOAD_CONCURRENCY" "$src" "$dst"
done

# Publish the manifest last, so it never advertises a set that isn't fully uploaded yet.
echo "==> $MANIFEST -> $DEST_DIR$REMOTE.manifest.json"
rclone copyto --progress "$MANIFEST" "$DEST_DIR$REMOTE.manifest.json"

echo "✓ Published ${VERSION}${SUFFIX} (image + direct-boot artifacts + manifest)"
