#!/usr/bin/env bash
# generate-operator-signing-key.sh — mint an RC-gate operator RSA key pair (testing helper).
#
# Standalone operator util (NOT part of the chutes-cvm CLI). The RC gate signs the attestation
# nonce inside the VM with the operator PRIVATE key and the API verifies it with the matching
# PUBLIC key, so a test run needs both halves:
#
#   • the PRIVATE key  → referenced from config.yaml as `rc.operator_signing_key` (a host path).
#                        `chutes-cvm guest launch` copies it onto the config volume as
#                        operator-signing-key.pem; the initramfs `rc-sign` hook signs with it
#                        (openssl dgst -sha256 -sign, RSA PKCS#1 v1.5).
#   • the PUBLIC key   → registered with the Chutes API (added to the accepted RC measurement
#                        values), so signatures from this VM verify as rc=true.
#
# This just wraps `openssl` so generating the pair and wiring it up is one command. It writes no
# config and registers nothing — it only prints where each half goes.
set -euo pipefail

PREFIX="operator-signing-key"
OUT_DIR="."
BITS=4096
FORCE=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [-o OUT_DIR] [-p PREFIX] [-b BITS] [-f]

Generate an RC-gate operator RSA key pair for testing.

Options:
  -o OUT_DIR   directory to write the keys into (default: current directory)
  -p PREFIX    base filename (default: ${PREFIX})
  -b BITS      RSA key size (default: ${BITS})
  -f           overwrite existing key files
  -h           show this help

Writes:
  <OUT_DIR>/<PREFIX>.pem       operator PRIVATE key (mode 0600)  → config rc.operator_signing_key
  <OUT_DIR>/<PREFIX>.pub.pem   operator PUBLIC key  (mode 0644)  → register with the Chutes API

Example:
  $(basename "$0") -o ~/rc-keys
  # then in config.yaml:
  #   rc:
  #     operator_signing_key: "\$HOME/rc-keys/${PREFIX}.pem"
EOF
}

while getopts ":o:p:b:fh" opt; do
    case "$opt" in
        o) OUT_DIR="$OPTARG" ;;
        p) PREFIX="$OPTARG" ;;
        b) BITS="$OPTARG" ;;
        f) FORCE=1 ;;
        h) usage; exit 0 ;;
        :) echo "Error: -$OPTARG requires an argument." >&2; usage; exit 2 ;;
        \?) echo "Error: unknown option -$OPTARG." >&2; usage; exit 2 ;;
    esac
done

command -v openssl >/dev/null 2>&1 || {
    echo "Error: openssl not found on PATH." >&2
    exit 1
}

case "$BITS" in
    2048|3072|4096) ;;
    *) echo "Error: BITS must be one of 2048, 3072, 4096 (got '$BITS')." >&2; exit 2 ;;
esac

mkdir -p "$OUT_DIR"
PRIV="$OUT_DIR/$PREFIX.pem"
PUB="$OUT_DIR/$PREFIX.pub.pem"

if [[ "$FORCE" -ne 1 ]]; then
    for f in "$PRIV" "$PUB"; do
        if [[ -e "$f" ]]; then
            echo "Error: $f already exists (pass -f to overwrite)." >&2
            exit 1
        fi
    done
fi

echo "Generating ${BITS}-bit RSA operator key pair..."
# genpkey → PKCS#8 PEM private key; pkey -pubout → SubjectPublicKeyInfo PEM public key.
# Both are what `openssl dgst -sha256 -sign/-verify` (the RC-gate signer/verifier) expect.
umask 077
openssl genpkey -algorithm RSA -pkeyopt "rsa_keygen_bits:$BITS" -out "$PRIV"
openssl pkey -in "$PRIV" -pubout -out "$PUB"
chmod 600 "$PRIV"
chmod 644 "$PUB"

# Absolute paths so they can be pasted straight into config.yaml.
PRIV_ABS="$(cd "$(dirname "$PRIV")" && pwd)/$(basename "$PRIV")"
PUB_ABS="$(cd "$(dirname "$PUB")" && pwd)/$(basename "$PUB")"

cat <<EOF

✓ Operator RC key pair written:
    private: $PRIV_ABS   (mode 0600)
    public : $PUB_ABS   (mode 0644)

Next steps:
  1. Point config.yaml at the PRIVATE key (host path — never inline the key):
       rc:
         operator_signing_key: "$PRIV_ABS"
     (or pass --operator-signing-key "$PRIV_ABS" to \`chutes-cvm guest launch\`)

  2. Register the PUBLIC key with the Chutes API — add it to the accepted RC measurement
     values so this VM's rc-sign signatures verify as rc=true:
$(sed 's/^/       /' "$PUB_ABS")
EOF
