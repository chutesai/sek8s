#!/bin/bash
set -e

# Log function
log() {
    echo "$1" >> /var/log/first-boot-tdx-attest.log
}

# Check if running in a TDX environment
log "Checking for TDX environment..."
if [ -f /sys/devices/virtual/dmi/id/board_name ] && grep -q "TDX" /sys/devices/virtual/dmi/id/board_name; then
    log "TDX environment detected, proceeding with attestation..."

    # Compute SHA256 hash of k3s binary
    K3S_BINARY="/usr/local/bin/k3s"
    if [ -f "$K3S_BINARY" ]; then
        K3S_HASH=$(sha256sum "$K3S_BINARY" | awk '{print $1}')
        log "Computed SHA256 hash of $K3S_BINARY: $K3S_HASH"
    else
        log "Error: $K3S_BINARY not found"
        exit 1
    fi

    # Compute SHA256 hash of etcd database directory (if it exists)
    ETCD_DIR="/var/lib/rancher/k3s/server/db/etcd"
    if [ -d "$ETCD_DIR" ]; then
        ETCD_HASH=$(find "$ETCD_DIR" -type f -exec sha256sum {} \; | sort | sha256sum | awk '{print $1}')
        log "Computed SHA256 hash of $ETCD_DIR contents: $ETCD_HASH"
    else
        ETCD_HASH="none"
        log "Warning: $ETCD_DIR not found, skipping etcd hash (database may be initialized later)"
    fi

    # Combine hashes for user data
    USER_DATA="$K3S_HASH:$ETCD_HASH"
    log "Combined user data for attestation: $USER_DATA"

    # Generate TD quote with user data
    log "Generating TD quote..."
    QUOTE_FILE="/var/run/tdx/quote.bin"
    mkdir -p /var/run/tdx
    if ! tdx-attest --generate-quote "$QUOTE_FILE" --user-data "$USER_DATA"; then
        log "Error: Failed to generate TD quote"
        exit 1
    fi
    log "Generated TD quote at $QUOTE_FILE"

    # Verify TD quote (replace with your attestation service endpoint)
    ATTESTATION_SERVICE="https://attestation.intel.com/verify" # Placeholder, use AWS or Intel endpoint
    log "Verifying TD quote with attestation service..."
    # Provide expected hashes (replace with your trusted values)
    EXPECTED_K3S_HASH="your-trusted-k3s-binary-sha256-hash" # Replace with actual k3s hash
    EXPECTED_ETCD_HASH="your-trusted-etcd-dir-sha256-hash"  # Replace with actual etcd hash or "none"
    EXPECTED_USER_DATA="$EXPECTED_K3S_HASH:$EXPECTED_ETCD_HASH"
    if ! tdx-attest --verify "$QUOTE_FILE" --service "$ATTESTATION_SERVICE" --expected-user-data "$EXPECTED_USER_DATA"; then
        log "Error: TD quote verification failed or k3s/etcd hashes mismatch"
        exit 1
    fi
    log "TD quote verification successful, k3s binary and etcd database integrity confirmed"
else
    log "No TDX environment detected (non-TDX hardware or local test), skipping attestation"
fi

log "TDX attestation setup completed."