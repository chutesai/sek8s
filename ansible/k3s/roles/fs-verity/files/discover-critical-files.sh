#!/bin/bash
# discover-critical-files.sh - Critical file discovery for fs-verity protection
# Used by both Ansible build process and runtime attestation
# This script itself should be protected with fs-verity to prevent tampering

set -euo pipefail

# Output format: one file path per line for easy processing
# Files are output in deterministic order for consistent results

# Function to safely find files (handle missing directories)
safe_find() {
    local path="$1"
    shift
    if [[ -d "$path" ]]; then
        find "$path" "$@" 2>/dev/null || true
    fi
}

# Function to check if file exists and is regular file
check_file() {
    local file="$1"
    if [[ -f "$file" ]]; then
        echo "$file"
    fi
}

# K3s binaries (primary targets)
safe_find /usr/local/bin -name "k3s" -type f

# OPA binaries
safe_find /usr/local/bin -name "opa" -type f

# Systemd service files (all services in system directory)
safe_find /etc/systemd/system -name "*.service" -type f

# Boot and initialization scripts
safe_find /root/scripts/boot -name "*.sh" -type f

# OPA policy files
safe_find /etc/opa/policies -name "*.rego" -type f -o -name "*.json" -type f

# Admission controller configuration files
safe_find /etc/admission-controller -type f \( -name "*.json" -o -name "*.yaml" -o -name "*.yml" -o -name "*.conf" \)

# K3s manifests (critical for cluster configuration)
safe_find /var/lib/rancher/k3s/server/manifests -name "*.yaml" -type f -o -name "*.yml" -type f

# Critical system configuration files (check if they exist)
check_file "/etc/security/limits.conf"
check_file "/etc/sysctl.conf"
check_file "/etc/audit/auditd.conf"
check_file "/etc/pam.d/common-auth"
check_file "/etc/pam.d/common-password"
check_file "/etc/ssh/sshd_config"
check_file "/etc/sudoers"

# Protect all binaries except sshd and sudo since they will be removed
safe_find /usr/bin -type f -executable ! -name sudo | sort
safe_find /usr/sbin -type f -executable ! -name sshd | sort

# This discovery script itself (important for runtime integrity)
check_file "/usr/local/bin/discover-critical-files.sh"