#!/bin/bash
# Collect module state for attestation reporting

set -e

OUTPUT_DIR="/var/lib/attestation"
CONFIG_FILE="/etc/attestation/config.yaml"
MODULE_STATE_FILE="/var/lib/attestation/module-state-current.json"
MAX_ATTESTATION_REPORTS=50  # Keep last 50 attestation reports

# Create comprehensive module state report
generate_module_attestation() {
    local timestamp=$(date -Iseconds)
    local report_file="$OUTPUT_DIR/module-attestation-$(date +%s).json"
    
    # Check if module-monitor has recent data
    local module_monitor_data="{}"
    if [ -f "$MODULE_STATE_FILE" ] && [ -s "$MODULE_STATE_FILE" ]; then
        # Check if file is less than 15 minutes old
        if [ $(find "$MODULE_STATE_FILE" -mmin -15 | wc -l) -gt 0 ]; then
            module_monitor_data=$(cat "$MODULE_STATE_FILE")
        fi
    fi
    
    # Collect various module states
    local modules_loaded=$(lsmod | tail -n +2 | awk '{print $1}' | sort | tr '\n' ' ')
    local modules_hash=$(lsmod | sha256sum | cut -d' ' -f1)
    local kernel_tainted=$(cat /proc/sys/kernel/tainted)
    local sig_enforce=$(grep -q 'module.sig_enforce=1' /proc/cmdline && echo "true" || echo "false")
    
    # Check for unsigned modules
    local unsigned_modules=""
    for mod in $(lsmod | tail -n +2 | awk '{print $1}'); do
        if ! modinfo "$mod" 2>/dev/null | grep -q "^sig_"; then
            unsigned_modules="$unsigned_modules $mod"
        fi
    done
    
    # Module file integrity
    local module_files_hash=$(find /lib/modules/$(uname -r) -name "*.ko*" -type f -exec sha256sum {} \; 2>/dev/null | sha256sum | cut -d' ' -f1)
    
    # Critical module presence check
    local critical_modules="overlay br_netfilter nf_conntrack iptable_nat iptable_filter"
    local missing_critical=""
    for mod in $critical_modules; do
        if ! lsmod | grep -q "^$mod"; then
            missing_critical="$missing_critical $mod"
        fi
    done
    
    # AppArmor status for module control
    local apparmor_enforced=$(aa-status 2>/dev/null | grep -c "enforce mode" || echo "0")
    
    # Create JSON report
    cat > "$report_file" << EOF
{
    "timestamp": "$timestamp",
    "hostname": "$(hostname)",
    "kernel_version": "$(uname -r)",
    "attestation_type": "module_security",
    "module_state": {
        "total_modules": $(lsmod | wc -l),
        "modules_hash": "$modules_hash",
        "module_files_hash": "$module_files_hash",
        "kernel_tainted": $kernel_tainted,
        "taint_flags": {
            "proprietary": $((kernel_tainted & 1)),
            "forced_module": $((kernel_tainted & 2)),
            "unsigned_module": $((kernel_tainted & 4096)),
            "out_of_tree": $((kernel_tainted & 8192))
        },
        "signature_enforcement": $sig_enforce,
        "unsigned_modules": [$(echo "$unsigned_modules" | sed 's/^ *//;s/ *$//;s/ /", "/g;s/^/"/;s/$/"/;s/""//')],
        "missing_critical": [$(echo "$missing_critical" | sed 's/^ *//;s/ *$//;s/ /", "/g;s/^/"/;s/$/"/;s/""//')],
        "apparmor_profiles": $apparmor_enforced
    },
    "measurements": {
        "boot_cmdline": "$(cat /proc/cmdline | sha256sum | cut -d' ' -f1)",
        "kernel_config": "$(cat /boot/config-$(uname -r) 2>/dev/null | sha256sum | cut -d' ' -f1 || echo 'unavailable')",
        "module_directory": "$(find /lib/modules/$(uname -r) -type f -name "*.ko*" | wc -l) files"
    },
    "security_checks": {
        "modules_disabled": $(cat /proc/sys/kernel/modules_disabled),
        "kexec_disabled": $(cat /proc/sys/kernel/kexec_load_disabled),
        "unprivileged_userns": $(cat /proc/sys/kernel/unprivileged_userns_clone),
        "bpf_disabled": $(cat /proc/sys/kernel/unprivileged_bpf_disabled)
    },
    "module_monitor_data": $module_monitor_data
}
EOF
    
    echo "$report_file"
}

# Send to attestation endpoint if configured
send_attestation() {
    local report_file="$1"
    
    if [ -f "$CONFIG_FILE" ]; then
        local endpoint=$(grep "endpoint:" "$CONFIG_FILE" | cut -d' ' -f2)
        local token=$(grep "token:" "$CONFIG_FILE" | cut -d' ' -f2)
        
        if [ -n "$endpoint" ] && [ -n "$token" ]; then
            curl -X POST \
                -H "Authorization: Bearer $token" \
                -H "Content-Type: application/json" \
                -d "@$report_file" \
                "$endpoint" \
                --silent \
                --show-error \
                --max-time 30 \
                > /var/log/attestation/last-upload.log 2>&1
            
            return $?
        fi
    fi
    
    return 0
}

cleanup_old_reports() {
    # Clean up old attestation reports
    if [ -d "$OUTPUT_DIR" ]; then
        local count=$(ls -1 "$OUTPUT_DIR"/module-attestation-*.json 2>/dev/null | wc -l)
        if [ $count -gt $MAX_ATTESTATION_REPORTS ]; then
            ls -t "$OUTPUT_DIR"/module-attestation-*.json | tail -n +$((MAX_ATTESTATION_REPORTS + 1)) | xargs rm -f
            echo "Cleaned up $((count - MAX_ATTESTATION_REPORTS)) old attestation reports"
        fi
    fi
}

# Main execution
echo "Collecting module attestation data..."
REPORT=$(generate_module_attestation)
echo "Generated report: $REPORT"

if send_attestation "$REPORT"; then
    echo "Attestation report sent successfully"
else
    echo "Failed to send attestation report" >&2
    exit 1
fi

# Clean up old reports
cleanup_old_reports