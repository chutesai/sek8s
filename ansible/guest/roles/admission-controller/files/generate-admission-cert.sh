#!/bin/bash
# /usr/local/bin/generate-admission-cert
#
# Generates a fresh per-VM TLS server certificate for the admission controller,
# signed by the stable build-time CA whose cert is embedded in the webhook
# manifest caBundle.
#
# Runs once at boot (generate-admission-cert.service) AFTER the storage bind-
# mounts are active (so /etc/admission-controller/certs is the storage copy
# already synced from root) and AFTER rtmr3-verify has confirmed RTMR3
# integrity.  Runs BEFORE admission-controller.service starts.
#
# A fresh key is generated on every boot so no private key material persists
# across reboots in the storage volume; on the next boot it will be overwritten
# again.
#
# The CA key and cert in /etc/admission-controller/certs come from the root
# filesystem (synced via rsync -ac in setup-storage-bind-mounts).  They are
# stable within a given image build version.

set -euo pipefail

CERTS_DIR=/etc/admission-controller/certs
OPENSSL_CNF="${CERTS_DIR}/openssl.cnf"
CA_KEY="${CERTS_DIR}/ca.key"
CA_CERT="${CERTS_DIR}/ca.crt"
SERVER_KEY="${CERTS_DIR}/server.key"
SERVER_CSR="${CERTS_DIR}/server.csr"
SERVER_CERT="${CERTS_DIR}/server.crt"

fatal() {
    echo "generate-admission-cert: FATAL: $1" >&2
    echo "generate-admission-cert: FATAL: $1" > /dev/kmsg 2>/dev/null || true
    exit 1
}

[ -f "${CA_KEY}" ]  || fatal "CA key not found: ${CA_KEY}"
[ -f "${CA_CERT}" ] || fatal "CA cert not found: ${CA_CERT}"
[ -f "${OPENSSL_CNF}" ] || fatal "OpenSSL config not found: ${OPENSSL_CNF}"

echo "generate-admission-cert: generating fresh per-VM server certificate ..."

# Always generate a new key — no 'creates' guard here.
openssl genrsa -out "${SERVER_KEY}" 4096 2>/dev/null
openssl req -new \
    -key "${SERVER_KEY}" \
    -out "${SERVER_CSR}" \
    -config "${OPENSSL_CNF}"
openssl x509 -req -days 365 \
    -in "${SERVER_CSR}" \
    -CA "${CA_CERT}" \
    -CAkey "${CA_KEY}" \
    -CAcreateserial \
    -out "${SERVER_CERT}" \
    -extensions v3_req \
    -extfile "${OPENSSL_CNF}"

rm -f "${SERVER_CSR}"

# Restrict permissions on key material.
chown root:admission "${SERVER_KEY}" "${SERVER_CERT}"
chmod 0640 "${SERVER_KEY}" "${SERVER_CERT}"

echo "generate-admission-cert: server certificate generated successfully"
