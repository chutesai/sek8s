#!/bin/bash
# Phase 3: Binary Integrity Checking System
set -e

INTEGRITY_DIR="/etc/security/integrity"
BASELINE_FILE="$INTEGRITY_DIR/binary-checksums.sha256"
REPORT_DIR="/var/lib/attestation"
LOG_FILE="/var/log/integrity-check.log"
ALERT_FILE="/var/log/integrity-alerts.log"
STATE_FILE="/var/lib/attestation/integrity-state-current.json"
MAX_REPORTS=20

# Critical binaries to monitor
CRITICAL_BINARIES=(
    "/usr/local/bin/k3s"
    "/usr/bin/kubectl"
    "/usr/bin/containerd"
    "/usr/bin/containerd-shim"
    "/usr/bin/containerd-shim-runc-v2"
    "/usr/bin/runc"
    "/usr/bin/docker"
    "/usr/bin/dockerd"
    "/usr/sbin/iptables"
    "/usr/sbin/ip6tables"
    "/usr/bin/ctr"
    "/usr/bin/crictl"
    "/bin/systemctl"
    "/bin/mount"
    "/bin/umount"
    "/usr/bin/nsenter"
)

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

alert() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ALERT: $1" | tee -a "$ALERT_FILE"
    # Send to system journal for monitoring
    logger -t binary-check -p security.warning "$1"
}

create_baseline() {
    log "Creating binary baseline..."
    mkdir -p "$INTEGRITY_DIR"
    > "$BASELINE_FILE"
    
    for binary in "${CRITICAL_BINARIES[@]}"; do
        if [ -f "$binary" ]; then
            sha256sum "$binary" >> "$BASELINE_FILE"
            log "Baselined: $binary"
        else
            log "Warning: Binary not found: $binary"
        fi
    done
        
    # Also hash k3s symlinks if they exist
    for link in /usr/local/bin/kubectl /usr/local/bin/crictl /usr/local/bin/ctr; do
        if [ -L "$link" ]; then
            target=$(readlink -f "$link")
            echo "# Symlink: $link -> $target" >> "$BASELINE_FILE"
        fi
    done
    
    log "Baseline created with $(wc -l < "$BASELINE_FILE") entries"
}

verify_integrity() {
    local violations=0
    local missing=0
    local modified=()
    
    if [ ! -f "$BASELINE_FILE" ]; then
        alert "No baseline file found! Run with --create-baseline first"
        return 1
    fi
    
    log "Starting binary verification..."
    
    while IFS= read -r line; do
        # Skip comments
        [[ "$line" =~ ^#.*$ ]] && continue
        [ -z "$line" ] && continue
        
        expected_hash=$(echo "$line" | awk '{print $1}')
        binary_path=$(echo "$line" | awk '{print $2}')
        
        if [ ! -f "$binary_path" ]; then
            alert "Binary missing: $binary_path"
            missing=$((missing + 1))
            continue
        fi
        
        current_hash=$(sha256sum "$binary_path" 2>/dev/null | awk '{print $1}')
        
        if [ "$current_hash" != "$expected_hash" ]; then
            alert "Binary modified: $binary_path"
            alert "  Expected: $expected_hash"
            alert "  Current:  $current_hash"
            modified+=("$binary_path")
            violations=$((violations + 1))
        fi
    done < "$BASELINE_FILE"
    
    # Check for new suspicious binaries in critical paths
    for dir in /usr/local/bin /usr/bin /usr/sbin /bin /sbin; do
        if [ -d "$dir" ]; then
            for binary in "$dir"/*; do
                [ ! -f "$binary" ] && continue
                # Check if it's setuid/setgid
                if [ -u "$binary" ] || [ -g "$binary" ]; then
                    if ! grep -q "$binary" "$BASELINE_FILE"; then
                        alert "New SUID/SGID binary detected: $binary"
                        violations=$((violations + 1))
                    fi
                fi
            done
        fi
    done
    
    return $violations
}

check_chroot_usage() {
    log "Scanning for chroot usage in boot scripts..."
    local found_chroot=0
    
    # Check systemd services for chroot
    for service in /etc/systemd/system/*.service /usr/lib/systemd/system/*.service; do
        if [ -f "$service" ]; then
            if grep -l "chroot\|RootDirectory=" "$service" >/dev/null 2>&1; then
                log "Service may use chroot: $(basename "$service")"
            fi
        fi
    done
    
    return $found_chroot
}

generate_report() {
    mkdir -p "$REPORT_DIR"
    local timestamp=$(date +%s)
    local report_file="$REPORT_DIR/binary-check-${timestamp}.json"
    
    # Run verification
    verify_integrity
    local integrity_status=$?
    
    check_chroot_usage
    local chroot_status=$?
    
    # Check if systemcall filter is active
    local syscall_filter="unknown"
    if systemctl show k3s -p SystemCallFilter | grep -q "~chroot"; then
        syscall_filter="active"
    else
        syscall_filter="inactive"
    fi
    
    # Check capability bounding set
    local cap_sys_chroot="unknown"
    if systemctl show k3s -p CapabilityBoundingSet | grep -q "~CAP_SYS_CHROOT"; then
        cap_sys_chroot="dropped"
    else
        cap_sys_chroot="present"
    fi
    
    cat > "$report_file" << EOF
{
    "timestamp": "$(date -Iseconds)",
    "hostname": "$(hostname)",
    "checks": {
        "binary_integrity": {
            "status": $([ $integrity_status -eq 0 ] && echo '"pass"' || echo '"fail"'),
            "violations": $integrity_status,
            "baseline_entries": $(wc -l < "$BASELINE_FILE" 2>/dev/null || echo 0)
        },
        "chroot_usage": {
            "boot_scripts": $chroot_status,
            "status": $([ $chroot_status -eq 0 ] && echo '"pass"' || echo '"fail"')
        },
        "systemd_hardening": {
            "syscall_filter_chroot": "$syscall_filter",
            "cap_sys_chroot": "$cap_sys_chroot",
            "no_new_privileges": "$(systemctl show k3s -p NoNewPrivileges | cut -d= -f2)"
        },
        "critical_binaries": {
            "k3s": "$([ -f /usr/local/bin/k3s ] && sha256sum /usr/local/bin/k3s | awk '{print $1}' || echo 'missing')",
            "containerd": "$([ -f /usr/bin/containerd ] && sha256sum /usr/bin/containerd | awk '{print $1}' || echo 'missing')"
        }
    }
}
EOF
    
    # Update current state
    cp "$report_file" "$STATE_FILE"
    
    # Clean up old reports
    local count=$(ls -1 "$REPORT_DIR"/binary-check-*.json 2>/dev/null | wc -l)
    if [ $count -gt $MAX_REPORTS ]; then
        ls -t "$REPORT_DIR"/binary-check-*.json | tail -n +$((MAX_REPORTS + 1)) | xargs rm -f
    fi
    
    echo "$report_file"
}

# Main execution
case "${1:-check}" in
    --create-baseline)
        create_baseline
        ;;
    --verify)
        verify_integrity
        exit $?
        ;;
    --check-chroot)
        check_chroot_usage
        exit $?
        ;;
    --report)
        REPORT=$(generate_report)
        log "Generated report: $REPORT"
        ;;
    check|*)
        # Default: run all checks and generate report
        REPORT=$(generate_report)
        log "Integrity check completed. Report: $REPORT"
        
        # Exit with error if violations found
        verify_integrity
        exit $?
        ;;
esac