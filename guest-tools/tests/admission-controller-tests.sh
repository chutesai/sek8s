#!/bin/bash
# Phase 4a - Admission Controller Test Suite
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test results
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_SKIPPED=0

# Log functions
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

log_skip() {
    echo -e "${BLUE}[SKIP]${NC} $1"
    TESTS_SKIPPED=$((TESTS_SKIPPED + 1))
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# Ensure we're running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root"
    exit 1
fi

echo "========================================="
echo "Phase 4a - Admission Controller Tests"
echo "========================================="

# Test namespace for all tests
TEST_NS="admission-test-$$"
CLEANUP_REQUIRED=false

# Cleanup function
cleanup() {
    if [ "$CLEANUP_REQUIRED" = true ]; then
        log_info "Cleaning up test namespace..."
        kubectl delete namespace "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# ============================================================================
# PREREQUISITE CHECKS
# ============================================================================

log_test "Checking OPA service"
if systemctl is-active --quiet opa; then
    log_pass "OPA service is running"
else
    log_fail "OPA service is not running"
    exit 1
fi

log_test "Checking admission controller service"
if systemctl is-active --quiet admission-controller; then
    log_pass "Admission controller service is running"
else
    log_fail "Admission controller service is not running"
    exit 1
fi

log_test "Checking OPA health endpoint"
if curl -s http://localhost:8181/health >/dev/null 2>&1; then
    log_pass "OPA health check passed"
else
    log_fail "OPA health check failed"
fi

log_test "Checking admission controller health endpoint"
if curl -sk https://localhost:8443/health >/dev/null 2>&1; then
    log_pass "Admission controller health check passed"
else
    log_fail "Admission controller health check failed"
fi

log_test "Checking ValidatingWebhookConfiguration"
if kubectl get validatingwebhookconfiguration admission-controller-webhook >/dev/null 2>&1; then
    log_pass "ValidatingWebhookConfiguration exists"
else
    log_fail "ValidatingWebhookConfiguration not found"
    log_info "You may need to apply the webhook configuration"
fi

# Create test namespace
log_info "Creating test namespace: $TEST_NS"
kubectl create namespace "$TEST_NS" >/dev/null 2>&1
CLEANUP_REQUIRED=true

# ============================================================================
# VOLUME MOUNT TESTS
# ============================================================================

echo -e "\n${YELLOW}Volume Mount Restrictions${NC}"

log_test "Test: Valid /cache mount should be allowed"
if kubectl apply -n "$TEST_NS" -f - <<EOF >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: valid-cache-mount
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    resources:
      limits:
        memory: "128Mi"
        cpu: "100m"
  volumes:
  - name: cache
    hostPath:
      path: /cache/test
      type: DirectoryOrCreate
  volumeMounts:
  - name: cache
    mountPath: /data
EOF
then
    log_pass "Pod with /cache mount was allowed"
    kubectl delete pod valid-cache-mount -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_fail "Pod with /cache mount was rejected"
fi

log_test "Test: Invalid /etc mount should be blocked"
if kubectl apply -n "$TEST_NS" -f - <<EOF 2>/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: invalid-etc-mount
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    resources:
      limits:
        memory: "128Mi"
  volumes:
  - name: etc
    hostPath:
      path: /etc
      type: Directory
  volumeMounts:
  - name: etc
    mountPath: /host-etc
EOF
then
    log_fail "Pod with /etc mount was allowed (should be blocked)"
    kubectl delete pod invalid-etc-mount -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_pass "Pod with /etc mount was blocked"
fi

log_test "Test: Job with emptyDir /tmp should be allowed"
if kubectl apply -n "$TEST_NS" -f - <<EOF >/dev/null 2>&1
apiVersion: batch/v1
kind: Job
metadata:
  name: job-with-emptydir
spec:
  template:
    spec:
      containers:
      - name: worker
        image: docker.io/library/busybox:latest
        command: ["sh", "-c", "echo 'test' > /tmp/output.txt"]
        resources:
          limits:
            memory: "128Mi"
        volumeMounts:
        - name: tmp
          mountPath: /tmp
      volumes:
      - name: tmp
        emptyDir: {}
      restartPolicy: Never
EOF
then
    log_pass "Job with emptyDir /tmp was allowed"
    kubectl delete job job-with-emptydir -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_fail "Job with emptyDir /tmp was rejected"
fi

# ============================================================================
# SECURITY CONTEXT TESTS
# ============================================================================

echo -e "\n${YELLOW}Security Context Restrictions${NC}"

log_test "Test: Privileged container should be blocked"
if kubectl apply -n "$TEST_NS" -f - <<EOF 2>/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: privileged-pod
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    securityContext:
      privileged: true
    resources:
      limits:
        memory: "128Mi"
EOF
then
    log_fail "Privileged pod was allowed (should be blocked)"
    kubectl delete pod privileged-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_pass "Privileged pod was blocked"
fi

log_test "Test: Host network pod should be blocked"
if kubectl apply -n "$TEST_NS" -f - <<EOF 2>/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: host-network-pod
spec:
  hostNetwork: true
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    resources:
      limits:
        memory: "128Mi"
EOF
then
    log_fail "Host network pod was allowed (should be blocked)"
    kubectl delete pod host-network-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_pass "Host network pod was blocked"
fi

log_test "Test: Non-privileged pod should be allowed"
if kubectl apply -n "$TEST_NS" -f - <<EOF >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: secure-pod
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      runAsNonRoot: true
      runAsUser: 1000
      capabilities:
        drop:
        - ALL
    resources:
      limits:
        memory: "128Mi"
        cpu: "100m"
EOF
then
    log_pass "Secure pod was allowed"
    kubectl delete pod secure-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_fail "Secure pod was rejected"
fi

# ============================================================================
# CAPABILITY TESTS
# ============================================================================

echo -e "\n${YELLOW}Capability Restrictions${NC}"

log_test "Test: CAP_SYS_ADMIN should be blocked"
if kubectl apply -n "$TEST_NS" -f - <<EOF 2>/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: cap-sys-admin-pod
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    securityContext:
      capabilities:
        add:
        - CAP_SYS_ADMIN
    resources:
      limits:
        memory: "128Mi"
EOF
then
    log_fail "Pod with CAP_SYS_ADMIN was allowed (should be blocked)"
    kubectl delete pod cap-sys-admin-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_pass "Pod with CAP_SYS_ADMIN was blocked"
fi

log_test "Test: NET_BIND_SERVICE should be allowed"
if kubectl apply -n "$TEST_NS" -f - <<EOF >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: cap-net-bind-pod
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    securityContext:
      capabilities:
        add:
        - NET_BIND_SERVICE
    resources:
      limits:
        memory: "128Mi"
EOF
then
    log_pass "Pod with NET_BIND_SERVICE was allowed"
    kubectl delete pod cap-net-bind-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_fail "Pod with NET_BIND_SERVICE was rejected"
fi

# ============================================================================
# REGISTRY TESTS
# ============================================================================

echo -e "\n${YELLOW}Registry Restrictions${NC}"

log_test "Test: Allowed registry (docker.io) should pass"
if kubectl apply -n "$TEST_NS" -f - <<EOF >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: allowed-registry-pod
spec:
  containers:
  - name: app
    image: docker.io/library/nginx:latest
    resources:
      limits:
        memory: "128Mi"
EOF
then
    log_pass "Pod from docker.io was allowed"
    kubectl delete pod allowed-registry-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_fail "Pod from docker.io was rejected"
fi

log_test "Test: Disallowed registry should be blocked"
if kubectl apply -n "$TEST_NS" -f - <<EOF 2>/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: untrusted-registry-pod
spec:
  containers:
  - name: app
    image: untrusted.registry.com/malicious:latest
    resources:
      limits:
        memory: "128Mi"
EOF
then
    log_fail "Pod from untrusted registry was allowed (should be blocked)"
    kubectl delete pod untrusted-registry-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_pass "Pod from untrusted registry was blocked"
fi

# ============================================================================
# RESOURCE LIMITS TESTS
# ============================================================================

echo -e "\n${YELLOW}Resource Limits${NC}"

log_test "Test: Pod without resource limits should be blocked"
if kubectl apply -n "$TEST_NS" -f - <<EOF 2>/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: no-limits-pod
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    # No resource limits
EOF
then
    log_fail "Pod without resource limits was allowed (should be blocked)"
    kubectl delete pod no-limits-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_pass "Pod without resource limits was blocked"
fi

log_test "Test: Pod with excessive CPU should be blocked"
if kubectl apply -n "$TEST_NS" -f - <<EOF 2>/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: excessive-cpu-pod
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    resources:
      limits:
        memory: "128Mi"
        cpu: "10000m"  # 10 CPUs - should be blocked
EOF
then
    log_fail "Pod with excessive CPU was allowed (should be blocked)"
    kubectl delete pod excessive-cpu-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_pass "Pod with excessive CPU was blocked"
fi

# ============================================================================
# EXEC/ATTACH BLOCKING TESTS
# ============================================================================

echo -e "\n${YELLOW}Exec/Attach Blocking${NC}"

# Create a test pod for exec testing
kubectl run exec-test-pod -n "$TEST_NS" --image=busybox --restart=Never -- sleep 300 >/dev/null 2>&1

# Wait for pod to be ready
sleep 3

log_test "Test: kubectl exec should be blocked"
if kubectl exec -n "$TEST_NS" exec-test-pod -- echo "exec worked" 2>/dev/null; then
    log_fail "kubectl exec was allowed (should be blocked)"
else
    log_pass "kubectl exec was blocked"
fi

log_test "Test: kubectl attach should be blocked"
if timeout 2 kubectl attach -n "$TEST_NS" exec-test-pod 2>/dev/null; then
    log_fail "kubectl attach was allowed (should be blocked)"
else
    log_pass "kubectl attach was blocked"
fi

# Cleanup exec test pod
kubectl delete pod exec-test-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1

# ============================================================================
# NAMESPACE OPERATIONS TESTS
# ============================================================================

echo -e "\n${YELLOW}Namespace Operations${NC}"

log_test "Test: Creating namespace should be blocked"
if kubectl create namespace test-create-ns-$$ 2>/dev/null; then
    log_fail "Namespace creation was allowed (should be blocked)"
    kubectl delete namespace test-create-ns-$$ --force --grace-period=0 >/dev/null 2>&1
else
    log_pass "Namespace creation was blocked"
fi

log_test "Test: Creating CRD should be blocked"
if kubectl apply -f - <<EOF 2>/dev/null
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: tests.example.com
spec:
  group: example.com
  versions:
  - name: v1
    served: true
    storage: true
    schema:
      openAPIV3Schema:
        type: object
  scope: Namespaced
  names:
    plural: tests
    singular: test
    kind: Test
EOF
then
    log_fail "CRD creation was allowed (should be blocked)"
    kubectl delete crd tests.example.com --force --grace-period=0 >/dev/null 2>&1
else
    log_pass "CRD creation was blocked"
fi

# ============================================================================
# ENVIRONMENT VARIABLE TESTS
# ============================================================================

echo -e "\n${YELLOW}Environment Variable Restrictions${NC}"

log_test "Test: HF_ENDPOINT environment variable should be allowed"
if kubectl apply -n "$TEST_NS" -f - <<EOF >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: hf-endpoint-pod
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    env:
    - name: HF_ENDPOINT
      value: "https://huggingface.co"
    - name: CUDA_VISIBLE_DEVICES
      value: "0,1"
    resources:
      limits:
        memory: "128Mi"
EOF
then
    log_pass "Pod with HF_ENDPOINT was allowed"
    kubectl delete pod hf-endpoint-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_fail "Pod with HF_ENDPOINT was rejected"
fi

log_test "Test: KUBECONFIG environment variable should be blocked"
if kubectl apply -n "$TEST_NS" -f - <<EOF 2>/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: kubeconfig-env-pod
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    env:
    - name: KUBECONFIG
      value: "/etc/kubernetes/admin.conf"
    resources:
      limits:
        memory: "128Mi"
EOF
then
    log_fail "Pod with KUBECONFIG env was allowed (should be blocked)"
    kubectl delete pod kubeconfig-env-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_pass "Pod with KUBECONFIG env was blocked"
fi

# ============================================================================
# DEPLOYMENT TESTS
# ============================================================================

echo -e "\n${YELLOW}Deployment Resource Tests${NC}"

log_test "Test: Valid deployment should be allowed"
if kubectl apply -n "$TEST_NS" -f - <<EOF >/dev/null 2>&1
apiVersion: apps/v1
kind: Deployment
metadata:
  name: valid-deployment
spec:
  replicas: 1
  selector:
    matchLabels:
      app: test
  template:
    metadata:
      labels:
        app: test
    spec:
      containers:
      - name: app
        image: docker.io/library/nginx:latest
        resources:
          limits:
            memory: "256Mi"
            cpu: "500m"
          requests:
            memory: "128Mi"
            cpu: "100m"
EOF
then
    log_pass "Valid deployment was allowed"
    kubectl delete deployment valid-deployment -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_fail "Valid deployment was rejected"
fi

log_test "Test: Deployment with privileged containers should be blocked"
if kubectl apply -n "$TEST_NS" -f - <<EOF 2>/dev/null
apiVersion: apps/v1
kind: Deployment
metadata:
  name: privileged-deployment
spec:
  replicas: 1
  selector:
    matchLabels:
      app: test
  template:
    metadata:
      labels:
        app: test
    spec:
      containers:
      - name: app
        image: docker.io/library/nginx:latest
        securityContext:
          privileged: true
        resources:
          limits:
            memory: "256Mi"
EOF
then
    log_fail "Privileged deployment was allowed (should be blocked)"
    kubectl delete deployment privileged-deployment -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_pass "Privileged deployment was blocked"
fi

# ============================================================================
# ENFORCEMENT MODE TESTS
# ============================================================================

echo -e "\n${YELLOW}Enforcement Mode Tests${NC}"

# Check current enforcement mode
ENFORCEMENT_MODE=$(curl -sk https://localhost:8443/health 2>/dev/null | grep -o '"enforcement_mode":"[^"]*"' | cut -d'"' -f4 || echo "unknown")
log_info "Current enforcement mode: $ENFORCEMENT_MODE"

# Test system namespace behavior
log_test "Test: System namespace (kube-system) policy check"
SYSTEM_NS_RESULT=$(kubectl apply -n kube-system --dry-run=server -f - <<EOF 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: system-test-pod
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    securityContext:
      privileged: true
    resources:
      limits:
        memory: "128Mi"
EOF
)

if echo "$SYSTEM_NS_RESULT" | grep -q "created\|configured"; then
    log_info "System namespace allows privileged pods (warn mode)"
elif echo "$SYSTEM_NS_RESULT" | grep -q "denied\|blocked"; then
    log_info "System namespace blocks privileged pods (enforce mode)"
else
    log_skip "Could not determine system namespace policy"
fi

# ============================================================================
# METRICS ENDPOINT TEST
# ============================================================================

echo -e "\n${YELLOW}Metrics and Monitoring${NC}"

log_test "Test: Metrics endpoint should be accessible"
if curl -sk https://localhost:8443/metrics | grep -q "admission_requests_total"; then
    log_pass "Metrics endpoint is working"
else
    log_fail "Metrics endpoint not working or missing metrics"
fi

# ============================================================================
# PERFORMANCE TEST
# ============================================================================

echo -e "\n${YELLOW}Performance Test${NC}"

log_test "Test: Admission latency check"
START_TIME=$(date +%s%N)
kubectl apply -n "$TEST_NS" --dry-run=server -f - <<EOF >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: perf-test
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    resources:
      limits:
        memory: "128Mi"
EOF
END_TIME=$(date +%s%N)
LATENCY=$(( (END_TIME - START_TIME) / 1000000 ))

if [ "$LATENCY" -lt 100 ]; then
    log_pass "Admission latency is good: ${LATENCY}ms"
elif [ "$LATENCY" -lt 500 ]; then
    log_info "Admission latency is acceptable: ${LATENCY}ms"
else
    log_fail "Admission latency is high: ${LATENCY}ms"
fi

# ============================================================================
# SUMMARY
# ============================================================================

echo "========================================="
echo "Test Results Summary"
echo "========================================="
echo -e "${GREEN}Passed:${NC} $TESTS_PASSED"
echo -e "${RED}Failed:${NC} $TESTS_FAILED"
echo -e "${BLUE}Skipped:${NC} $TESTS_SKIPPED"

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    echo "Phase 4a admission controller is working correctly."
    exit 0
else
    echo -e "${YELLOW}Some tests failed. Review the output above for details.${NC}"
    echo "Common issues:"
    echo "- Ensure webhook configuration is applied"
    echo "- Check OPA policies are loaded correctly"
    echo "- Verify services are running and healthy"
    echo "- Check logs: journalctl -u admission-controller -n 50"
    exit 1
fi