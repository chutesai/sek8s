#!/bin/bash
# Module monitoring script for attestation and security

set -e

BASELINE_DIR="/etc/module-security"
REPORT_DIR="/var/lib/attestation"
LOG_FILE="/var/log/module-monitor.log"
STATE_FILE="/var/lib/attestation/module-state-current.json"
MAX_REPORTS=20  # Keep only last 20 reports

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

check_loaded_modules() {
    local current_modules=$(lsmod | sort)
    local baseline_modules=$(cat "$BASELINE_DIR/modules.baseline")
    
    if [ "$current_modules" != "$baseline_modules" ]; then
        log "WARNING: Loaded modules differ from baseline"
        diff <(echo "$baseline_modules") <(echo "$current_modules") >> "$LOG_FILE" 2>&1 || true
        return 1
    fi
    return 0
}

check_module_files() {
    local current_hashes=$(find /lib/modules/$(uname -r) -name "*.ko*" -exec sha256sum {} \; | sort)
    local baseline_hashes=$(cat "$BASELINE_DIR/module-files.sha256")
    
    if [ "$current_hashes" != "$baseline_hashes" ]; then
        log "WARNING: Module files have been modified"
        diff <(echo "$baseline_hashes") <(echo "$current_hashes") >> "$LOG_FILE" 2>&1 || true
        return 1
    fi
    return 0
}

check_tainted_modules() {
    # Check for tainted modules (unsigned, out-of-tree, etc.)
    local tainted=$(cat /proc/sys/kernel/tainted)
    
    if [ "$tainted" != "0" ]; then
        log "WARNING: Kernel is tainted: $tainted"
        
        # Decode taint flags
        if [ $((tainted & 4096)) -ne 0 ]; then
            log "  - Unsigned module loaded"
        fi
        if [ $((tainted & 512)) -ne 0 ]; then
            log "  - Kernel warning"
        fi
        if [ $((tainted & 32768)) -ne 0 ]; then
            log "  - Kernel has been live patched"
        fi
        
        return 1
    fi
    return 0
}

generate_report() {
    mkdir -p "$REPORT_DIR"
    
    # Generate report with timestamp
    local timestamp=$(date +%s)
    local report_file="$REPORT_DIR/module-state-${timestamp}.json"
    
    cat > "$report_file" << EOF
{
    "timestamp": "$(date -Iseconds)",
    "hostname": "$(hostname)",
    "kernel": "$(uname -r)",
    "module_count": $(lsmod | wc -l),
    "modules_hash": "$(lsmod | sha256sum | cut -d' ' -f1)",
    "module_files_hash": "$(find /lib/modules/$(uname -r) -name "*.ko*" -exec sha256sum {} \; | sha256sum | cut -d' ' -f1)",
    "kernel_tainted": $(cat /proc/sys/kernel/tainted),
    "signature_enforcement": $(grep -q 'module.sig_enforce=1' /proc/cmdline && echo true || echo false),
    "modules_loaded": [
$(lsmod | tail -n +2 | awk '{printf "        \"%s\"", $1}' | sed 's/,$//' | tr '\n' ',' | sed 's/,$/\n/')
    ]
}
EOF
    
    # Also update the current state file (always latest)
    cp "$report_file" "$STATE_FILE"
    
    echo "$report_file"
}

cleanup_old_reports() {
    # Clean up old module-state reports (keep only last MAX_REPORTS)
    if [ -d "$REPORT_DIR" ]; then
        # Count and remove old module-state files
        local count=$(ls -1 "$REPORT_DIR"/module-state-*.json 2>/dev/null | wc -l)
        if [ $count -gt $MAX_REPORTS ]; then
            ls -t "$REPORT_DIR"/module-state-*.json | tail -n +$((MAX_REPORTS + 1)) | xargs rm -f
            log "Cleaned up $((count - MAX_REPORTS)) old reports"
        fi
    fi
}

# Main execution
log "Starting module security check"

STATUS=0

if ! check_loaded_modules; then
    STATUS=1
fi

if ! check_module_files; then
    STATUS=1
fi

if ! check_tainted_modules; then
    STATUS=1
fi

# Generate report for attestation
REPORT=$(generate_report)
log "Generated report: $REPORT"

# Clean up old reports
cleanup_old_reports

# Alert if issues detected
if [ $STATUS -ne 0 ]; then
    log "Module security issues detected, check logs for details"
    # Could trigger additional alerts here
fi

exit $STATUS