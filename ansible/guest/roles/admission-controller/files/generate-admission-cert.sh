#!/bin/bash
# /usr/local/bin/generate-admission-cert
#
# Generates the per-VM admission-controller TLS material AND wires the matching
# CA into the webhook manifests — all fresh on every boot, none of it persisted
# in the root image, so none of it is measured into RTMR3.
#
# Boot ordering (generate-admission-cert.service):
#   After=setup-storage-bind-mounts.service   certs dir + manifests are the
#                                             storage bind-mount copies
#   After=rtmr3-verify.service                RTMR3 confirmed against the
#                                             *placeholder* manifests first
#   Before=k3s.service                        inject the real caBundle BEFORE
#                                             k3s ever applies the webhook
#   Before=admission-controller.service       server cert ready before serving
#
# Why every boot, and why this exact window:
#   The webhook manifests are baked with a deterministic placeholder caBundle
#   (__ADMISSION_CA_BUNDLE__) so the root image — and therefore RTMR3 — is
#   reproducible across builds.  setup-storage-bind-mounts rsyncs that
#   placeholder copy into the storage volume on every boot, and rtmr3-verify
#   then confirms the storage copy still matches the measured root copy.  ONLY
#   AFTER that check passes do we mint an ephemeral CA and substitute the real
#   base64(ca.crt) into the storage copy — the one k3s actually applies.
#   setup-storage-bind-mounts has already evicted the k3s addon tracking from
#   kine, so k3s re-applies the (now real-CA) manifest on start.  Injecting any
#   earlier would make rtmr3-verify hash a manifest that diverges from the
#   measurement and power the VM off.
#
# A fresh CA + server key are generated on every boot so no private key material
# ever persists where it could be measured; next boot the rsync resets the
# manifests back to the placeholder and the whole chain is rebuilt.

set -euo pipefail

CERTS_DIR=/etc/admission-controller/certs
OPENSSL_CNF="${CERTS_DIR}/openssl.cnf"
CA_KEY="${CERTS_DIR}/ca.key"
CA_CERT="${CERTS_DIR}/ca.crt"
SERVER_KEY="${CERTS_DIR}/server.key"
SERVER_CSR="${CERTS_DIR}/server.csr"
SERVER_CERT="${CERTS_DIR}/server.crt"

MANIFEST_DIR=/var/lib/rancher/k3s/server/manifests
CA_BUNDLE_PLACEHOLDER='__ADMISSION_CA_BUNDLE__'
WEBHOOK_MANIFESTS=(
    "${MANIFEST_DIR}/validating-webhook.yaml"
    "${MANIFEST_DIR}/mutation-webhook.yaml"
)

# In production a fatal powers the VM off: the admission webhook cannot be
# wired up, so the node must not run workloads. On debug builds
# (GENERATE_ADMISSION_CERT_DEBUG_MODE=true, set via /etc/default/generate-admission-cert)
# it logs and RETURNS so the VM stays up for troubleshooting — mirrors
# rtmr3-verify / gpu-verify.
fatal() {
    echo "generate-admission-cert: FATAL: $1" >&2
    echo "generate-admission-cert: FATAL: $1" > /dev/kmsg 2>/dev/null || true
    if [ "${GENERATE_ADMISSION_CERT_DEBUG_MODE:-false}" = "true" ]; then
        echo "generate-admission-cert: DEBUG MODE — would power off but continuing" >&2
        return 0
    fi
    sleep 1
    systemctl poweroff --force
    exit 1
}

[ -f "${OPENSSL_CNF}" ] || fatal "OpenSSL config not found: ${OPENSSL_CNF}"

# ── Ephemeral CA — fresh every boot, never written to the measured root image ──
echo "generate-admission-cert: generating ephemeral per-VM CA ..."
openssl genrsa -out "${CA_KEY}" 4096 2>/dev/null
openssl req -new -x509 \
    -key "${CA_KEY}" \
    -out "${CA_CERT}" \
    -days 3650 \
    -subj "/CN=admission-controller-ca"

# ── Per-VM server certificate, signed by the ephemeral CA ─────────────────────
echo "generate-admission-cert: generating per-VM server certificate ..."
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

# Lock down key material; the CA cert and server cert are world-readable (public).
chown root:root "${CA_KEY}"
chmod 0600 "${CA_KEY}"
chown root:admission "${SERVER_KEY}" "${SERVER_CERT}" "${CA_CERT}"
chmod 0640 "${SERVER_KEY}"
chmod 0644 "${CA_CERT}" "${SERVER_CERT}"

# ── Inject the matching caBundle into the webhook manifests ───────────────────
# k8s caBundle is base64(PEM).  The measured root manifest carries only the
# placeholder; we substitute the real value into the storage (bind-mounted) copy
# that k3s applies.  The manifest is trusted at this point — rtmr3-verify ran
# first and confirmed it matches the measurement.
CA_BUNDLE="$(base64 -w0 "${CA_CERT}")"

for manifest in "${WEBHOOK_MANIFESTS[@]}"; do
    if [ ! -f "${manifest}" ]; then
        # At runtime the webhook manifests are ALWAYS present: baked into the
        # image and rsynced to the storage volume by setup-storage-bind-mounts
        # before this runs. A missing manifest means a broken image or boot
        # sequence — fail closed rather than leave the webhook pointing at a
        # placeholder CA the apiserver can never validate.
        #
        # This is not the build-time path: this script only runs at boot. The
        # service Requires rtmr3-verify + setup-storage-bind-mounts, neither of
        # which is installed yet when the build starts the admission controller
        # for its health check, so generate-admission-cert never fires there.
        fatal "webhook manifest not found: ${manifest}"
        continue  # reached only in debug mode (fatal returned)
    fi
    # rsync -a does not replicate the +i immutable flag, but clear it defensively
    # in case a future rsync variant or manual chattr leaves it set.
    chattr -i "${manifest}" 2>/dev/null || true
    if grep -q "${CA_BUNDLE_PLACEHOLDER}" "${manifest}"; then
        sed -i "s|${CA_BUNDLE_PLACEHOLDER}|${CA_BUNDLE}|g" "${manifest}"
        echo "generate-admission-cert: injected caBundle into ${manifest}"
    else
        # No placeholder left: the service is being re-run within the same boot
        # (rsync resets to the placeholder only across reboots). Idempotent —
        # leave the already-injected caBundle in place.
        echo "generate-admission-cert: no placeholder in ${manifest}, already injected (re-run), skipping"
    fi
done

echo "generate-admission-cert: TLS material and webhook caBundle ready"
