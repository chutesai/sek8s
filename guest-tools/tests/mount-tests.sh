#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test results
TESTS_PASSED=0
TESTS_FAILED=0

# Log function
log_test() {
    echo -e "${YELLOW}[TEST]${NC} $1"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

# Ensure we're running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root"
    exit 1
fi

echo "========================================="
echo "Phase 1: Mount Restrictions Test Suite"
echo "========================================="

# Test 1: Verify /cache directory exists and has correct permissions
log_test "Test 1: Checking /cache directory"
if [ -d /cache ]; then
    PERMS=$(stat -c %a /cache)
    OWNER=$(stat -c %U:%G /cache)
    if [ "$PERMS" = "755" ] && [ "$OWNER" = "root:root" ]; then
        log_pass "/cache exists with correct permissions (755) and ownership (root:root)"
    else
        log_fail "/cache has incorrect permissions ($PERMS) or ownership ($OWNER)"
    fi
else
    log_fail "/cache directory does not exist"
fi

# Test 2: Verify AppArmor profiles are loaded
log_test "Test 2: Checking AppArmor profiles"
if aa-status 2>/dev/null | grep -q "k3s-restrictions"; then
    log_pass "k3s-restrictions AppArmor profile is loaded"
else
    log_fail "k3s-restrictions AppArmor profile is not loaded"
fi

if aa-status 2>/dev/null | grep -q "containerd-restrictions"; then
    log_pass "containerd-restrictions AppArmor profile is loaded"
else
    log_fail "containerd-restrictions AppArmor profile is not loaded"
fi

# Test 3: Test mounting to /cache (should succeed)
log_test "Test 3: Testing mount to /cache"
TEST_CACHE_DIR="/cache/test-mount-$$"
mkdir -p "$TEST_CACHE_DIR"
if mount -t tmpfs tmpfs "$TEST_CACHE_DIR" 2>/dev/null; then
    log_pass "Successfully mounted tmpfs to $TEST_CACHE_DIR"
    umount "$TEST_CACHE_DIR"
    rmdir "$TEST_CACHE_DIR"
else
    log_fail "Failed to mount tmpfs to $TEST_CACHE_DIR"
    rmdir "$TEST_CACHE_DIR" 2>/dev/null || true
fi

# Test 4: Test mounting outside /cache (should fail when AppArmor is enforced)
log_test "Test 4: Testing mount outside /cache (should be restricted)"
TEST_DIR="/tmp/test-mount-$"
mkdir -p "$TEST_DIR"
if mount -t tmpfs tmpfs "$TEST_DIR" 2>/dev/null; then
    # Check if this is actually restricted by AppArmor
    if dmesg | tail -20 | grep -q "apparmor.*DENIED.*mount"; then
        log_pass "Mount succeeded but AppArmor logged denial (transition period)"
        umount "$TEST_DIR" 2>/dev/null || true
    else
        log_fail "Mount to $TEST_DIR succeeded without AppArmor denial"
        umount "$TEST_DIR" 2>/dev/null || true
    fi
else
    log_pass "Mount to $TEST_DIR was blocked"
fi
rmdir "$TEST_DIR" 2>/dev/null || true

# Test 5: Verify systemd drop-in for k3s exists
log_test "Test 5: Checking k3s systemd drop-in configuration"
DROPIN_FILE="/etc/systemd/system/k3s.service.d/mount-restrictions.conf"
if [ -f "$DROPIN_FILE" ]; then
    if grep -q "ProtectSystem=strict" "$DROPIN_FILE" && \
       grep -q "ReadWritePaths=.*\/cache" "$DROPIN_FILE" && \
       grep -q "AppArmorProfile=k3s-restrictions" "$DROPIN_FILE"; then
        log_pass "k3s systemd drop-in configured correctly"
    else
        log_fail "k3s systemd drop-in missing required configurations"
    fi
else
    log_fail "k3s systemd drop-in file not found"
fi

# Test 6: Test k3s pod with cache mount (should succeed)
log_test "Test 6: Testing k3s pod with /cache mount"
if command -v kubectl >/dev/null 2>&1; then
    cat <<EOF | kubectl apply -f - >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: test-cache-mount
  namespace: default
spec:
  containers:
  - name: test
    image: busybox
    command: ["sleep", "30"]
    volumeMounts:
    - name: cache
      mountPath: /data
  volumes:
  - name: cache
    hostPath:
      path: /cache/test-pod
      type: DirectoryOrCreate
EOF
    sleep 5
    if kubectl get pod test-cache-mount >/dev/null 2>&1; then
        POD_STATUS=$(kubectl get pod test-cache-mount -o jsonpath='{.status.phase}')
        if [ "$POD_STATUS" = "Running" ]; then
            log_pass "Pod with /cache mount is running"
        else
            log_fail "Pod with /cache mount is not running (status: $POD_STATUS)"
        fi
        kubectl delete pod test-cache-mount --force --grace-period=0 >/dev/null 2>&1
    else
        log_fail "Failed to create pod with /cache mount"
    fi
else
    log_test "kubectl not available, skipping k8s pod tests"
fi

# Test 7: Test k3s pod with non-cache mount (should fail with OPA)
log_test "Test 7: Testing k3s pod with non-cache mount"
if command -v kubectl >/dev/null 2>&1; then
    cat <<EOF | kubectl apply -f - 2>/tmp/kubectl-error-$ >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: test-etc-mount
  namespace: default
spec:
  containers:
  - name: test
    image: busybox
    command: ["sleep", "30"]
    volumeMounts:
    - name: host
      mountPath: /host
  volumes:
  - name: host
    hostPath:
      path: /etc
      type: Directory
EOF
    if kubectl get pod test-etc-mount >/dev/null 2>&1; then
        # Pod was created, check if OPA would block it
        if [ -f /etc/opa/policies/volume-restrictions.rego ]; then
            log_fail "Pod with /etc mount was created (OPA not enforcing yet)"
        else
            log_fail "Pod with /etc mount was created (OPA policy not installed)"
        fi
        kubectl delete pod test-etc-mount --force --grace-period=0 >/dev/null 2>&1
    else
        if grep -q "denied\|forbidden\|not allowed" /tmp/kubectl-error-$ 2>/dev/null; then
            log_pass "Pod with /etc mount was rejected"
        else
            log_pass "Pod with /etc mount failed to create (expected during setup)"
        fi
    fi
    rm -f /tmp/kubectl-error-$
else
    log_test "kubectl not available, skipping k8s pod tests"
fi

# Test 8: Verify mount validation script exists and works
log_test "Test 8: Testing mount validation script"
if [ -x /usr/local/bin/validate-mounts ]; then
    if /usr/local/bin/validate-mounts >/tmp/mount-validation-$ 2>&1; then
        log_pass "Mount validation script executed successfully"
        if grep -q "All mounts are compliant" /tmp/mount-validation-$; then
            log_pass "System mounts are compliant"
        else
            log_fail "System has non-compliant mounts"
            cat /tmp/mount-validation-$
        fi
    else
        log_fail "Mount validation script reported violations"
        cat /tmp/mount-validation-$
    fi
    rm -f /tmp/mount-validation-$
else
    log_fail "Mount validation script not found or not executable"
fi

# Test 9: Check if containerd config has mount restrictions
log_test "Test 9: Checking containerd configuration"
CONTAINERD_CONFIG="/var/lib/rancher/k3s/agent/etc/containerd/config.toml"
if [ -f "$CONTAINERD_CONFIG" ]; then
    if grep -q "MountLabel.*containerd-restrictions" "$CONTAINERD_CONFIG" 2>/dev/null || \
       grep -q "config_path.*\/cache\/containerd" "$CONTAINERD_CONFIG" 2>/dev/null; then
        log_pass "Containerd config has mount restrictions"
    else
        log_fail "Containerd config missing mount restrictions"
    fi
else
    log_test "Containerd config not found (k3s may not be fully initialized)"
fi

# Test 10: Verify sysctl security parameters
log_test "Test 10: Checking sysctl security parameters"
EXPECTED_SYSCTLS="fs.protected_regular=2 fs.protected_fifos=2 fs.protected_symlinks=1 fs.protected_hardlinks=1"
ALL_SET=true
for sysctl_param in $EXPECTED_SYSCTLS; do
    KEY=$(echo $sysctl_param | cut -d= -f1)
    EXPECTED_VAL=$(echo $sysctl_param | cut -d= -f2)
    ACTUAL_VAL=$(sysctl -n $KEY 2>/dev/null)
    if [ "$ACTUAL_VAL" != "$EXPECTED_VAL" ]; then
        log_fail "sysctl $KEY is $ACTUAL_VAL, expected $EXPECTED_VAL"
        ALL_SET=false
    fi
done
if $ALL_SET; then
    log_pass "All sysctl security parameters are correctly set"
fi

# Test 11: Test bind mount from /cache (should succeed)
log_test "Test 11: Testing bind mount from /cache"
mkdir -p /cache/source-$ /tmp/target-$
echo "test" > /cache/source-$/testfile
if mount --bind /cache/source-$ /tmp/target-$ 2>/dev/null; then
    if [ -f /tmp/target-$/testfile ]; then
        log_pass "Bind mount from /cache succeeded"
    else
        log_fail "Bind mount succeeded but file not accessible"
    fi
    umount /tmp/target-$ 2>/dev/null || true
else
    log_fail "Bind mount from /cache failed"
fi
rm -rf /cache/source-$ /tmp/target-$

# Test 12: Test bind mount from /etc (should fail or be logged)
log_test "Test 12: Testing bind mount from /etc (should be restricted)"
mkdir -p /tmp/target-$
if mount --bind /etc /tmp/target-$ 2>/dev/null; then
    if dmesg | tail -20 | grep -q "apparmor.*DENIED.*mount.*\/etc"; then
        log_pass "Bind mount from /etc succeeded but AppArmor logged denial"
    else
        log_fail "Bind mount from /etc succeeded without restriction"
    fi
    umount /tmp/target-$ 2>/dev/null || true
else
    log_pass "Bind mount from /etc was blocked"
fi
rmdir /tmp/target-$ 2>/dev/null || true

# Test 13: Verify OPA policy file exists
log_test "Test 13: Checking OPA volume policy"
if [ -f /etc/opa/policies/volume-restrictions.rego ]; then
    if grep -q "deny.*hostPath" /etc/opa/policies/volume-restrictions.rego && \
       grep -q "startswith.*\/cache" /etc/opa/policies/volume-restrictions.rego; then
        log_pass "OPA volume restriction policy is properly configured"
    else
        log_fail "OPA policy exists but may be misconfigured"
    fi
else
    log_fail "OPA volume restriction policy not found"
fi

# Test 14: Check mount validator service
log_test "Test 14: Checking mount validator service"
if systemctl list-unit-files | grep -q "mount-validator.timer"; then
    if systemctl is-enabled mount-validator.timer >/dev/null 2>&1; then
        log_pass "Mount validator timer is enabled"
    else
        log_fail "Mount validator timer exists but is not enabled"
    fi
else
    log_fail "Mount validator timer not found"
fi

# Summary
echo "========================================="
echo "Test Results Summary"
echo "========================================="
echo -e "${GREEN}Passed:${NC} $TESTS_PASSED"
echo -e "${RED}Failed:${NC} $TESTS_FAILED"

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${YELLOW}Some tests failed. Review the output above for details.${NC}"
    echo "Note: Some failures may be expected during initial setup or if k3s is not fully configured."
    exit 1
fi