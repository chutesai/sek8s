#!/bin/bash
# runtime-fs-verity-check.sh - Runtime attestation using shared discovery script
# This script can be deployed separately for ongoing filesystem monitoring

set -euo pipefail

# Paths
DISCOVERY_SCRIPT="/usr/local/bin/discover-critical-files.sh"
BASELINE_MEASUREMENTS="/etc/fs-verity-measurements.txt"
LOG_FILE="/var/log/fs-verity-attestation.log"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Check if baseline measurements file exists
if [[ ! -f "$BASELINE_MEASUREMENTS" ]]; then
    log "ERROR: Baseline measurements file not found: $BASELINE_MEASUREMENTS"
    exit 1
fi

# Verify discovery script exists and is fs-verity protected
if [[ ! -f "$DISCOVERY_SCRIPT" ]]; then
    log "ERROR: Discovery script not found: $DISCOVERY_SCRIPT"
    exit 1
fi

# Verify the discovery script itself hasn't been tampered with
if ! fsverity measure "$DISCOVERY_SCRIPT" >/dev/null 2>&1; then
    log "ERROR: Discovery script is not fs-verity protected - potential security breach!"
    exit 1
fi

log "Starting runtime fs-verity attestation check"

# Get current critical files using the same discovery logic as build
CURRENT_FILES=$("$DISCOVERY_SCRIPT" | sort)
VIOLATIONS=0

# Create temporary file for current measurements
CURRENT_MEASUREMENTS=$(mktemp)
trap "rm -f '$CURRENT_MEASUREMENTS'" EXIT

# Get measurements for all currently discovered files
while IFS= read -r file; do
    if [[ -f "$file" ]]; then
        MEASUREMENT=$(fsverity measure "$file" 2>/dev/null || echo "UNPROTECTED")
        echo "$MEASUREMENT $file" >> "$CURRENT_MEASUREMENTS"
    else
        log "WARNING: File discovered by script but doesn't exist: $file"
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done <<< "$CURRENT_FILES"

# Compare with baseline measurements
log "Comparing current measurements with baseline..."

while IFS= read -r line; do
    # Skip comments and empty lines
    [[ "$line" =~ ^#.*$ ]] && continue
    [[ -z "$line" ]] && continue
    
    EXPECTED_MEASUREMENT=$(echo "$line" | awk '{print $1}')
    FILE_PATH=$(echo "$line" | awk '{print $2}')
    
    if [[ -f "$FILE_PATH" ]]; then
        CURRENT_MEASUREMENT=$(fsverity measure "$FILE_PATH" 2>/dev/null | awk '{print $1}' || echo "UNPROTECTED")
        
        if [[ "$CURRENT_MEASUREMENT" != "$EXPECTED_MEASUREMENT" ]]; then
            log "VIOLATION: File measurement mismatch for $FILE_PATH"
            log "  Expected: $EXPECTED_MEASUREMENT"
            log "  Current:  $CURRENT_MEASUREMENT"
            VIOLATIONS=$((VIOLATIONS + 1))
        fi
    else
        log "VIOLATION: Expected file missing: $FILE_PATH"
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done < "$BASELINE_MEASUREMENTS"

# Check for new files not in baseline (potential malicious additions)
NEW_FILES=0
while IFS= read -r measurement file; do
    if [[ "$measurement" != "UNPROTECTED" ]]; then
        if ! grep -q "$file" "$BASELINE_MEASUREMENTS"; then
            log "WARNING: New fs-verity protected file not in baseline: $file"
            NEW_FILES=$((NEW_FILES + 1))
        fi
    fi
done < "$CURRENT_MEASUREMENTS"

# Summary
log "Attestation check complete:"
log "  Violations: $VIOLATIONS"
log "  New protected files: $NEW_FILES"

if [[ $VIOLATIONS -eq 0 ]]; then
    log "SUCCESS: All fs-verity measurements match baseline"
    exit 0
else
    log "FAILURE: $VIOLATIONS integrity violations detected"
    exit 1
fi