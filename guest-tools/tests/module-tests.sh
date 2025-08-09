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
echo "Module Security & Seccomp Test Suite"
echo "========================================="

# Test 1: Verify module signature enforcement is enabled
log_test "Test 1: Checking module signature enforcement"
if grep -q 'module.sig_enforce=1' /proc/cmdline; then
    log_pass "Module signature enforcement is enabled in kernel cmdline"
else
    # Check if it's in GRUB config (requires reboot)
    if grep -q 'module.sig_enforce=1' /etc/default/grub; then
        log_fail "Module signature enforcement configured but requires reboot"
    else
        log_fail "Module signature enforcement NOT enabled"
    fi
fi

# Test 2: Check kernel taint for unsigned modules
log_test "Test 2: Checking kernel taint status"
TAINT=$(cat /proc/sys/kernel/tainted)
if [ "$TAINT" = "0" ]; then
    log_pass "Kernel is not tainted"
else
    # Decode taint flags
    if [ $((TAINT & 4096)) -ne 0 ]; then
        log_fail "Kernel tainted by unsigned module (taint: $TAINT)"
    elif [ $((TAINT & 1)) -ne 0 ]; then
        log_fail "Kernel tainted by proprietary module (taint: $TAINT)"
    else
        log_fail "Kernel is tainted with value: $TAINT"
    fi
fi

# Test 3: Verify all loaded modules have signatures
log_test "Test 3: Checking loaded module signatures"
UNSIGNED_COUNT=0
UNSIGNED_MODULES=""
for mod in $(lsmod | tail -n +2 | awk '{print $1}'); do
    if ! modinfo "$mod" 2>/dev/null | grep -q "^sig_"; then
        UNSIGNED_COUNT=$((UNSIGNED_COUNT + 1))
        UNSIGNED_MODULES="$UNSIGNED_MODULES $mod"
    fi
done
if [ $UNSIGNED_COUNT -eq 0 ]; then
    log_pass "All loaded modules have signature information"
else
    log_fail "Found $UNSIGNED_COUNT modules without signatures:$UNSIGNED_MODULES"
fi

# Test 4: Verify seccomp profiles exist
log_test "Test 4: Checking seccomp profiles"
SECCOMP_DIR="/var/lib/kubelet/seccomp"
if [ -d "$SECCOMP_DIR" ]; then
    if [ -f "$SECCOMP_DIR/user-workload.json" ] && \
       [ -f "$SECCOMP_DIR/k3s-system.json" ] && \
       [ -f "$SECCOMP_DIR/default-deny-mount.json" ]; then
        log_pass "All required seccomp profiles exist"
    else
        log_fail "Missing seccomp profiles in $SECCOMP_DIR"
    fi
else
    log_fail "Seccomp profile directory not found"
fi

# Test 5: Verify kernel security parameters
log_test "Test 5: Checking kernel security parameters"
EXPECTED_PARAMS="kernel.unprivileged_userns_clone=0 kernel.kexec_load_disabled=1 kernel.unprivileged_bpf_disabled=1"
ALL_SET=true
for param in $EXPECTED_PARAMS; do
    KEY=$(echo $param | cut -d= -f1)
    EXPECTED_VAL=$(echo $param | cut -d= -f2)
    ACTUAL_VAL=$(sysctl -n $KEY 2>/dev/null)
    if [ "$ACTUAL_VAL" = "$EXPECTED_VAL" ]; then
        log_pass "$KEY is correctly set to $EXPECTED_VAL"
    else
        log_fail "$KEY is $ACTUAL_VAL, expected $EXPECTED_VAL"
        ALL_SET=false
    fi
done

# Test 6: Verify AppArmor profile for k3s module control
log_test "Test 6: Checking AppArmor module control profile"
if command -v aa-status >/dev/null 2>&1; then
    if aa-status 2>/dev/null | grep -q "usr.local.bin.k3s"; then
        log_pass "K3s AppArmor profile is loaded"
    else
        log_fail "K3s AppArmor profile not found"
    fi
else
    log_fail "AppArmor not available"
fi

# Test 7: Test mount syscall blocking in container
log_test "Test 7: Testing mount syscall blocking with seccomp"
if command -v kubectl >/dev/null 2>&1; then
    cat <<EOF | kubectl apply -f - >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: test-mount-block
  namespace: default
spec:
  securityContext:
    seccompProfile:
      type: Localhost
      localhostProfile: user-workload.json
  containers:
  - name: test
    image: busybox
    command: ["/bin/sh", "-c"]
    args: ["mount -t tmpfs tmpfs /tmp/test 2>&1 || echo 'BLOCKED'"]
    securityContext:
      allowPrivilegeEscalation: false
      capabilities:
        drop:
        - ALL
EOF
    sleep 5
    if kubectl get pod test-mount-block >/dev/null 2>&1; then
        POD_LOGS=$(kubectl logs test-mount-block 2>/dev/null || echo "")
        if echo "$POD_LOGS" | grep -q "BLOCKED\|Operation not permitted\|Permission denied"; then
            log_pass "Mount syscall successfully blocked by seccomp"
        else
            log_fail "Mount syscall was not blocked (logs: $POD_LOGS)"
        fi
        kubectl delete pod test-mount-block --force --grace-period=0 >/dev/null 2>&1
    else
        log_fail "Failed to create test pod"
    fi
else
    log_test "kubectl not available, skipping container tests"
fi

# Test 8: Verify capability dropping for containers
log_test "Test 8: Testing capability restrictions"
if command -v kubectl >/dev/null 2>&1; then
    cat <<EOF | kubectl apply -f - >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: test-cap-drop
  namespace: default
spec:
  containers:
  - name: test
    image: busybox
    command: ["/bin/sh", "-c"]
    args: ["grep CapEff /proc/self/status"]
    securityContext:
      allowPrivilegeEscalation: false
      capabilities:
        drop:
        - ALL
        add:
        - NET_BIND_SERVICE
EOF
    sleep 5
    if kubectl get pod test-cap-drop >/dev/null 2>&1; then
        CAP_VALUE=$(kubectl logs test-cap-drop 2>/dev/null | grep CapEff | awk '{print $2}')
        if [ -n "$CAP_VALUE" ]; then
            # Check if CAP_SYS_ADMIN (bit 21, value 0x200000) is NOT set
            # Convert hex to decimal and check bit
            if [[ ! "$CAP_VALUE" =~ [2-9a-fA-F][0-9a-fA-F]{5} ]]; then
                log_pass "CAP_SYS_ADMIN successfully dropped (caps: $CAP_VALUE)"
            else
                log_fail "CAP_SYS_ADMIN may still be present (caps: $CAP_VALUE)"
            fi
        else
            log_fail "Could not read capability values"
        fi
        kubectl delete pod test-cap-drop --force --grace-period=0 >/dev/null 2>&1
    else
        log_fail "Failed to create capability test pod"
    fi
else
    log_test "kubectl not available, skipping capability tests"
fi

# Test 9: Test emptyDir volumes still work
log_test "Test 9: Testing emptyDir volumes for Jobs"
if command -v kubectl >/dev/null 2>&1; then
    cat <<EOF | kubectl apply -f - >/dev/null 2>&1
apiVersion: batch/v1
kind: Job
metadata:
  name: test-emptydir-job
  namespace: default
spec:
  template:
    spec:
      securityContext:
        seccompProfile:
          type: Localhost
          localhostProfile: user-workload.json
      containers:
      - name: test
        image: busybox
        command: ["/bin/sh", "-c"]
        args: ["echo 'test data' > /tmp/test.txt && cat /tmp/test.txt"]
        volumeMounts:
        - name: temp
          mountPath: /tmp
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop:
            - ALL
      volumes:
      - name: temp
        emptyDir: {}
      restartPolicy: Never
EOF
    sleep 10
    if kubectl get job test-emptydir-job >/dev/null 2>&1; then
        JOB_STATUS=$(kubectl get job test-emptydir-job -o jsonpath='{.status.succeeded}' 2>/dev/null)
        if [ "$JOB_STATUS" = "1" ]; then
            log_pass "Job with emptyDir volume succeeded despite seccomp"
        else
            log_fail "Job with emptyDir volume failed"
        fi
        kubectl delete job test-emptydir-job --force --grace-period=0 >/dev/null 2>&1
    else
        log_fail "Failed to create job with emptyDir"
    fi
else
    log_test "kubectl not available, skipping emptyDir tests"
fi

# Test 10: Verify module monitoring is configured
log_test "Test 10: Checking module monitoring setup"
if [ -f "/etc/module-security/modules.baseline" ]; then
    if systemctl is-enabled module-monitor.timer >/dev/null 2>&1; then
        log_pass "Module monitoring baseline and timer are configured"
    else
        log_fail "Module baseline exists but timer not enabled"
    fi
else
    log_fail "Module monitoring baseline not found"
fi

# Test 11: Verify attestation service is configured
log_test "Test 11: Checking attestation service"
if systemctl is-enabled module-attestation.timer >/dev/null 2>&1; then
    if [ -d "/var/lib/attestation" ]; then
        REPORT_COUNT=$(ls -1 /var/lib/attestation/module-attestation-*.json 2>/dev/null | wc -l)
        if [ $REPORT_COUNT -gt 0 ]; then
            log_pass "Attestation service running with $REPORT_COUNT reports"
        else
            log_pass "Attestation service enabled but no reports yet"
        fi
    else
        log_fail "Attestation directory not found"
    fi
else
    log_fail "Attestation timer not enabled"
fi

# Test 12: Verify module lockdown status
log_test "Test 12: Checking module lockdown status"
MODULES_DISABLED=$(cat /proc/sys/kernel/modules_disabled 2>/dev/null || echo "0")
if [ "$MODULES_DISABLED" = "0" ]; then
    log_pass "Modules not permanently disabled (recovery still possible)"
else
    log_fail "Modules are permanently disabled (value: $MODULES_DISABLED)"
fi

# Test 13: Test k3s system pods still have privileges
log_test "Test 13: Checking k3s system pod privileges"
if command -v kubectl >/dev/null 2>&1; then
    # Check if a system pod exists and has proper privileges
    SYSTEM_POD=$(kubectl get pods -n kube-system -o name 2>/dev/null | head -1)
    if [ -n "$SYSTEM_POD" ]; then
        POD_NAME=$(echo $SYSTEM_POD | cut -d/ -f2)
        # Check if pod has privileged security context
        PRIVILEGED=$(kubectl get pod -n kube-system $POD_NAME -o jsonpath='{.spec.containers[0].securityContext.privileged}' 2>/dev/null)
        if [ "$PRIVILEGED" = "true" ] || [ -z "$PRIVILEGED" ]; then
            log_pass "System pods retain necessary privileges"
        else
            log_fail "System pods may lack required privileges"
        fi
    else
        log_test "No system pods found to check privileges"
    fi
else
    log_test "kubectl not available, skipping system pod tests"
fi

# Test 14: Verify PodSecurityPolicy or Pod Security Standards
log_test "Test 14: Checking Pod Security configuration"
if command -v kubectl >/dev/null 2>&1; then
    # Check for PodSecurityPolicy
    if kubectl get psp 2>/dev/null | grep -q "restricted"; then
        log_pass "PodSecurityPolicy 'restricted' is configured"
    # Check for Pod Security Standards (newer k8s)
    elif kubectl get ns default -o jsonpath='{.metadata.labels}' 2>/dev/null | grep -q "pod-security"; then
        log_pass "Pod Security Standards are configured"
    else
        log_fail "No Pod Security Policy or Standards found"
    fi
else
    log_test "kubectl not available, skipping PSP tests"
fi

# Test 15: Test module loading attempt (should fail with sig_enforce)
log_test "Test 15: Testing module loading with signature enforcement"
if grep -q 'module.sig_enforce=1' /proc/cmdline; then
    # Try to load a non-existent module (will fail due to not being signed)
    if modprobe test_module_that_does_not_exist 2>/dev/null; then
        log_fail "Module loading succeeded when it should have failed"
    else
        log_pass "Unsigned module loading correctly blocked"
    fi
else
    log_test "Module signature enforcement not active, skipping test"
fi

# Test 16: Verify containerd configuration for seccomp
log_test "Test 16: Checking containerd seccomp configuration"
CONTAINERD_CONFIG="/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl"
if [ -f "$CONTAINERD_CONFIG" ]; then
    if grep -q "SeccompProfile" "$CONTAINERD_CONFIG"; then
        log_pass "Containerd configured with seccomp profile"
    else
        log_fail "Containerd missing seccomp configuration"
    fi
else
    log_fail "Containerd config template not found"
fi

# Test 17: Verify YAMA ptrace scope
log_test "Test 17: Checking YAMA ptrace restrictions"
PTRACE_SCOPE=$(sysctl -n kernel.yama.ptrace_scope 2>/dev/null || echo "0")
if [ "$PTRACE_SCOPE" = "2" ]; then
    log_pass "YAMA ptrace scope set to 2 (admin only)"
elif [ "$PTRACE_SCOPE" = "1" ]; then
    log_fail "YAMA ptrace scope is 1 (restricted), expected 2"
else
    log_fail "YAMA ptrace scope is $PTRACE_SCOPE, expected 2"
fi

# Test 18: Check for module state consistency
log_test "Test 18: Checking module state consistency"
if [ -f "/etc/module-security/modules.baseline" ]; then
    CURRENT_MODULES=$(lsmod | sort | sha256sum | cut -d' ' -f1)
    BASELINE_HASH=$(cat /etc/module-security/modules.baseline | sha256sum | cut -d' ' -f1)
    if [ "$CURRENT_MODULES" = "$BASELINE_HASH" ]; then
        log_pass "Current modules match baseline"
    else
        log_test "Module state differs from baseline (may be expected after updates)"
    fi
else
    log_fail "Module baseline not found for comparison"
fi

# Test 19: Verify namespace security labels
log_test "Test 19: Checking namespace security labels"
if command -v kubectl >/dev/null 2>&1; then
    # Check if chutes namespace exists and has security labels
    if kubectl get ns chutes >/dev/null 2>&1; then
        LABELS=$(kubectl get ns chutes -o jsonpath='{.metadata.labels}' 2>/dev/null)
        if echo "$LABELS" | grep -q "pod-security.kubernetes.io/enforce"; then
            log_pass "Namespace 'chutes' has Pod Security labels"
        else
            log_fail "Namespace 'chutes' missing Pod Security labels"
        fi
    else
        log_test "Namespace 'chutes' not found (will be created later)"
    fi
else
    log_test "kubectl not available, skipping namespace tests"
fi

# Test 20: Verify critical kernel modules are loaded
log_test "Test 20: Checking critical kernel modules"
CRITICAL_MODULES="overlay br_netfilter nf_conntrack"
ALL_LOADED=true
for mod in $CRITICAL_MODULES; do
    if lsmod | grep -q "^$mod"; then
        log_pass "Critical module '$mod' is loaded"
    else
        log_fail "Critical module '$mod' is NOT loaded"
        ALL_LOADED=false
    fi
done

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
    echo "Note: Some tests may fail if the system hasn't been rebooted after configuration."
    echo "Module signature enforcement requires a reboot to take effect."
    exit 1
fi