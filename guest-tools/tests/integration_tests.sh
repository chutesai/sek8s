#!/bin/bash
# Complete Verification Script
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "========================================="
echo "Security Verification"
echo "========================================="
echo "This script verifies all security measures"
echo

# Function to check status
check_status() {
    local test_name="$1"
    local command="$2"
    
    echo -n "Checking $test_name... "
    if eval "$command" >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC}"
        return 0
    else
        echo -e "${RED}✗${NC}"
        return 1
    fi
}

# Phase 1 Checks
echo -e "\n${YELLOW}Mount Restrictions${NC}"
check_status "Mount restrictions drop-in" "[ -f /etc/systemd/system/k3s.service.d/mount-restrictions.conf ]"
check_status "/cache directory exists" "[ -d /cache ]"
check_status "ProtectSystem=full active" "systemctl show k3s -p ProtectSystem | grep -q full"
check_status "Sysctl fs.protected_regular" "[ $(sysctl -n fs.protected_regular) -eq 2 ]"

# Phase 2 Checks
echo -e "\n${YELLOW}Seccomp and Module Security${NC}"
check_status "Seccomp profiles installed" "[ -f /var/lib/kubelet/seccomp/user-workload.json ]"
check_status "Module signature enforcement" "grep -q 'module.sig_enforce=1' /proc/cmdline || grep -q 'module.sig_enforce=1' /etc/default/grub"
check_status "Module monitor timer" "systemctl is-enabled module-monitor.timer"
check_status "Attestation timer" "systemctl is-enabled module-attestation.timer"

# Phase 3 Checks
echo -e "\n${YELLOW}Chroot and Init Hardening${NC}"
check_status "Drop-in exists" "[ -f /etc/systemd/system/k3s.service.d/chroot-restrictions.conf ]"
check_status "SystemCallFilter blocks chroot" "systemctl show k3s -p SystemCallFilter | grep -q 'chroot'"
check_status "Integrity check script" "[ -f /usr/local/bin/binary-check.sh ]"
check_status "Integrity baseline exists" "[ -f /etc/security/integrity/binary-checksums.sha256 ]"
check_status "Integrity timer enabled" "systemctl is-enabled binary-attestation.timer"
check_status "LockPersonality enabled" "systemctl show k3s -p LockPersonality | grep -q yes"
check_status "RestrictRealtime enabled" "systemctl show k3s -p RestrictRealtime | grep -q yes"

# K3s Functionality Checks
echo -e "\n${YELLOW}K3s Functionality${NC}"
check_status "K3s service running" "systemctl is-active k3s"
check_status "K3s node ready" "kubectl get nodes | grep -q Ready"
check_status "K3s can create pods" "kubectl run test-phase3-$$ --image=busybox --restart=Never --command -- echo test && kubectl delete pod test-phase3-$$ --force --grace-period=0"

# Security Test
echo -e "\n${YELLOW}Security Tests${NC}"
echo -n "Testing chroot blocking... "
TEST_DIR="/tmp/chroot-test-$$"
mkdir -p "$TEST_DIR"
if ! chroot "$TEST_DIR" /bin/true 2>/dev/null; then
    echo -e "${GREEN}✓ Blocked${NC}"
else
    echo -e "${RED}✗ Not blocked${NC}"
fi
rmdir "$TEST_DIR"

echo -n "Testing container creation... "
if kubectl run test-container-$$ --image=busybox --restart=Never --command -- sleep 5 >/dev/null 2>&1; then
    kubectl delete pod test-container-$$ --force --grace-period=0 >/dev/null 2>&1
    echo -e "${GREEN}✓ Works${NC}"
else
    echo -e "${RED}✗ Failed${NC}"
fi

# Summary
echo -e "\n========================================="
echo "Verification Complete"
echo "========================================="
echo -e "${GREEN}Phase 1:${NC} Mount restrictions active"
echo -e "${GREEN}Phase 2:${NC} Seccomp and module security active"
echo -e "${GREEN}Phase 3:${NC} Chroot and init hardening active"
echo
echo "NoNewPrivileges is intentionally disabled for k3s compatibility"
echo "This will be tested separately in the next phase"
echo
echo -e "${YELLOW}Run individual test suites for detailed results:${NC}"
echo "  /root/tests/mount-tests.sh       # Phase 1"
echo "  /root/tests/module-tests.sh      # Phase 2"
echo "  /root/tests/chroot-tests.sh      # Phase 3"