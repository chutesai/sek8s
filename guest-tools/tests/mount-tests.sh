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

# Test 2: Verify systemd drop-in for k3s exists
log_test "Test 2: Checking k3s systemd drop-in configuration"
DROPIN_FILE="/etc/systemd/system/k3s.service.d/mount-restrictions.conf"
if [ -f "$DROPIN_FILE" ]; then
    if grep -q "ProtectSystem=full" "$DROPIN_FILE" && \
       grep -q "ReadWritePaths=.*\/cache" "$DROPIN_FILE" && \
       ! grep -q "^AppArmorProfile=" "$DROPIN_FILE"; then
        log_pass "k3s systemd drop-in configured correctly"
    else
        log_fail "k3s systemd drop-in missing required configurations or has AppArmor enabled"
    fi
else
    log_fail "k3s systemd drop-in file not found"
fi

# Test 3: Test mounting to /cache (should succeed)
log_test "Test 3: Testing mount to /cache"
TEST_CACHE_DIR="/cache/test-mount-$"
mkdir -p "$TEST_CACHE_DIR"
if mount -t tmpfs tmpfs "$TEST_CACHE_DIR" 2>/dev/null; then
    log_pass "Successfully mounted tmpfs to $TEST_CACHE_DIR"
    umount "$TEST_CACHE_DIR"
    rmdir "$TEST_CACHE_DIR"
else
    log_fail "Failed to mount tmpfs to $TEST_CACHE_DIR"
    rmdir "$TEST_CACHE_DIR" 2>/dev/null || true
fi

# Test 4: Test mounting outside /cache (should work at OS level but be restricted for k3s)
log_test "Test 4: Testing mount outside /cache"
TEST_DIR="/tmp/test-mount-$"
mkdir -p "$TEST_DIR"
if mount -t tmpfs tmpfs "$TEST_DIR" 2>/dev/null; then
    log_pass "Mount to $TEST_DIR succeeded at OS level (systemd restrictions apply to k3s process only)"
    umount "$TEST_DIR" 2>/dev/null || true
else
    log_fail "Mount to $TEST_DIR failed at OS level"
fi
rmdir "$TEST_DIR" 2>/dev/null || true

# Test 5: Verify k3s service is running with restrictions
log_test "Test 5: Checking k3s service with mount restrictions"
if systemctl is-active k3s >/dev/null 2>&1; then
    # Check if ProtectSystem is active
    if systemctl show k3s -p ProtectSystem | grep -q "ProtectSystem=full"; then
        log_pass "k3s service running with ProtectSystem=full"
    else
        log_fail "k3s service running but without ProtectSystem=full"
    fi
else
    log_fail "k3s service is not running"
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

# Test 7: Test k3s pod with non-cache mount (OPA should block when configured)
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
        # Pod was created - this is expected without OPA
        log_pass "Pod with /etc mount was created (OPA not configured - will be blocked by OPA in Phase 4)"
        kubectl delete pod test-etc-mount --force --grace-period=0 >/dev/null 2>&1
    else
        log_fail "Pod with /etc mount failed to create unexpectedly"
    fi
    rm -f /tmp/kubectl-error-$
else
    log_test "kubectl not available, skipping k8s pod tests"
fi

# Test 8: Test k3s job with emptyDir for /tmp (should succeed)
log_test "Test 8: Testing k3s job with emptyDir for /tmp"
if command -v kubectl >/dev/null 2>&1; then
    cat <<EOF | kubectl apply -f - >/dev/null 2>&1
apiVersion: batch/v1
kind: Job
metadata:
  name: test-job-tmp
  namespace: default
spec:
  template:
    spec:
      containers:
      - name: test
        image: busybox
        command: ["sh", "-c", "echo 'test' > /tmp/test.txt && cat /tmp/test.txt"]
        volumeMounts:
        - name: tmp
          mountPath: /tmp
      volumes:
      - name: tmp
        emptyDir: {}
      restartPolicy: Never
EOF
    sleep 5
    if kubectl get job test-job-tmp >/dev/null 2>&1; then
        JOB_STATUS=$(kubectl get job test-job-tmp -o jsonpath='{.status.succeeded}')
        if [ "$JOB_STATUS" = "1" ]; then
            log_pass "Job with emptyDir /tmp mount succeeded"
        else
            log_fail "Job with emptyDir /tmp mount did not complete successfully"
        fi
        kubectl delete job test-job-tmp --force --grace-period=0 >/dev/null 2>&1
    else
        log_fail "Failed to create job with emptyDir /tmp mount"
    fi
else
    log_test "kubectl not available, skipping k8s job tests"
fi

# Test 9: Check systemd security parameters
log_test "Test 9: Checking systemd security parameters"
EXPECTED_PARAMS="ProtectSystem=full ProtectHome=yes PrivateMounts=yes MountFlags=slave"
ALL_SET=true
for param in $EXPECTED_PARAMS; do
    KEY=$(echo $param | cut -d= -f1)
    VAL=$(echo $param | cut -d= -f2)
    if systemctl show k3s -p $KEY | grep -q "$KEY=$VAL"; then
        log_pass "k3s has $param set correctly"
    else
        log_fail "k3s missing or incorrect $param"
        ALL_SET=false
    fi
done

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

# Test 12: Verify OPA policy file exists (for future Phase 4)
log_test "Test 12: Checking OPA volume policy"
if [ -f /etc/opa/policies/volume-restrictions.rego ]; then
    log_pass "OPA volume restriction policy exists"
else
    log_test "OPA volume restriction policy not found (will be added in Phase 4)"
fi

# Test 13: Verify no AppArmor profiles are enforced for k3s
log_test "Test 13: Checking AppArmor is not applied to k3s"
if command -v aa-status >/dev/null 2>&1; then
    if aa-status 2>/dev/null | grep -q "k3s-restrictions"; then
        log_fail "AppArmor k3s-restrictions profile is loaded (should not be used with systemd restrictions)"
    else
        log_pass "No AppArmor profile applied to k3s (using systemd restrictions instead)"
    fi
else
    log_pass "AppArmor not installed or not active"
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
    echo "Note: Test 7 will show pod creation succeeding until OPA is configured in Phase 4."
    exit 1
fi>/dev/null || true
fi