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

# Protect ALL of these directories recursively
# Skip sudo and sshd since they will be removed
echo "# Complete system binaries and libraries"
safe_find /usr -type f ! -wholename /usr/bin/sudo ! -name sshd | sort

echo "# System configuration"  
safe_find /etc -type f | sort

echo "# Third-party applications"
safe_find /opt -type f | sort

echo "# Boot files"
safe_find /boot -type f | sort

echo "# Root user files"  
safe_find /root/scripts -type f | sort
safe_find /root/.cosign -type f | sort

echo "# Service data"
safe_find /srv -type f | sort

echo "# Selected var content"
safe_find /var/lib/rancher/k3s/server/manifests -type f | sort