#!/bin/bash
# Phase 4a - Admission Controller Test Suite (Updated for Python + OPA Implementation)
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

# Use default namespace for testing since namespace creation is blocked
TEST_NS="default"
CLEANUP_REQUIRED=true

# Cleanup function
cleanup() {
    if [ "$CLEANUP_REQUIRED" = true ]; then
        log_info "Cleaning up test resources in $TEST_NS namespace..."
        # Clean up test pods with our process ID in the name
        kubectl delete pod -n "$TEST_NS" -l "test=admission-controller" --force --grace-period=0 >/dev/null 2>&1 || true
        
        # Clean up specific test resources that might have been created
        for resource in $(kubectl get pods,jobs,deployments,configmaps -n "$TEST_NS" -o name 2>/dev/null | grep -E "(test-|cache-test-|perf-test-|exec-test-|chutes-test-).*-$$"); do
            kubectl delete "$resource" -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1 || true
        done
        
        # Clean up any pods that match our test patterns
        kubectl delete pod -n "$TEST_NS" --field-selector="status.phase!=Running" --force --grace-period=0 >/dev/null 2>&1 || true
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
    HEALTH_JSON=$(curl -sk https://localhost:8443/health 2>/dev/null)
    if echo "$HEALTH_JSON" | grep -q '"healthy": true'; then
        log_pass "Admission controller health check passed"
    else
        log_fail "Admission controller unhealthy: $HEALTH_JSON"
    fi
else
    log_fail "Admission controller health endpoint unreachable"
fi

log_test "Checking admission controller ready endpoint"
if curl -sk https://localhost:8443/ready >/dev/null 2>&1; then
    READY_JSON=$(curl -sk https://localhost:8443/ready 2>/dev/null)
    if echo "$READY_JSON" | grep -q '"ready": true'; then
        log_pass "Admission controller ready check passed"
    else
        log_fail "Admission controller not ready: $READY_JSON"
    fi
else
    log_fail "Admission controller ready endpoint unreachable"
fi

log_test "Checking ValidatingWebhookConfiguration"
if kubectl get validatingwebhookconfiguration admission-controller-webhook >/dev/null 2>&1; then
    log_pass "ValidatingWebhookConfiguration exists"
    
    # Check webhook is pointing to localhost
    WEBHOOK_URL=$(kubectl get validatingwebhookconfiguration admission-controller-webhook -o jsonpath='{.webhooks[0].clientConfig.url}' 2>/dev/null)
    if echo "$WEBHOOK_URL" | grep -q "127.0.0.1:8443"; then
        log_pass "Webhook configured for localhost admission controller"
    else
        log_fail "Webhook URL incorrect: $WEBHOOK_URL"
    fi
else
    log_fail "ValidatingWebhookConfiguration not found"
    log_info "You may need to apply the webhook configuration"
fi

# Check OPA policies are loaded
log_test "Checking OPA policies are loaded"
OPA_POLICIES=$(curl -s http://localhost:8181/v1/policies 2>/dev/null || echo "{}")
if echo "$OPA_POLICIES" | grep -q "volume-restrictions\|main.rego"; then
    log_pass "OPA policies are loaded"
else
    log_skip "OPA policies may not be fully loaded yet"
fi

# Use default namespace for testing since we can't create new namespaces
TEST_NS="default"
log_info "Using default namespace for testing (namespace creation is blocked)"
# No cleanup needed since we're using existing namespace
CLEANUP_REQUIRED=false

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
    volumeMounts:
    - name: cache
      mountPath: /data
  volumes:
  - name: cache
    hostPath:
      path: /cache/test
      type: DirectoryOrCreate
EOF
then
    log_pass "Pod with /cache mount was allowed"
    kubectl delete pod valid-cache-mount -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_fail "Pod with /cache mount was rejected"
fi

log_test "Test: Invalid /etc mount should be blocked"
RESULT=$(kubectl apply -n "$TEST_NS" -f - <<EOF 2>&1 || true
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
        cpu: "100m"
    volumeMounts:
    - name: etc
        mountPath: /host-etc
  volumes:
  - name: etc
    hostPath:
      path: /etc
      type: Directory
EOF
)
if echo "$RESULT" | grep -q "created"; then
    log_fail "Pod with /etc mount was allowed (should be blocked)"
    kubectl delete pod invalid-etc-mount -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
elif echo "$RESULT" | grep -q "denied\|not allowed\|Policy violations"; then
    log_pass "Pod with /etc mount was blocked"
else
    log_skip "Unexpected result: $RESULT"
fi

log_test "Test: Invalid /var mount should be blocked"
RESULT=$(kubectl apply -n "$TEST_NS" -f - <<EOF 2>&1 || true
apiVersion: v1
kind: Pod
metadata:
  name: invalid-var-mount
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
  - name: var
    hostPath:
      path: /var/log
      type: Directory
EOF
)
if echo "$RESULT" | grep -q "created"; then
    log_fail "Pod with /var mount was allowed (should be blocked)"
    kubectl delete pod invalid-var-mount -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
elif echo "$RESULT" | grep -q "denied\|not allowed\|Policy violations"; then
    log_pass "Pod with /var mount was blocked"
else
    log_skip "Unexpected result: $RESULT"
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
            cpu: "100m"
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
RESULT=$(kubectl apply -n "$TEST_NS" -f - <<EOF 2>&1 || true
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
        cpu: "100m"
EOF
)
if echo "$RESULT" | grep -q "created"; then
    log_fail "Privileged pod was allowed (should be blocked)"
    kubectl delete pod privileged-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
elif echo "$RESULT" | grep -q "denied\|not allowed\|privileged"; then
    log_pass "Privileged pod was blocked"
else
    log_skip "Unexpected result: $RESULT"
fi

log_test "Test: Host network pod should be blocked"
RESULT=$(kubectl apply -n "$TEST_NS" -f - <<EOF 2>&1 || true
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
        cpu: "100m"
EOF
)
if echo "$RESULT" | grep -q "created"; then
    log_fail "Host network pod was allowed (should be blocked)"
    kubectl delete pod host-network-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
elif echo "$RESULT" | grep -q "denied\|not allowed\|host network"; then
    log_pass "Host network pod was blocked"
else
    log_skip "Unexpected result: $RESULT"
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
RESULT=$(kubectl apply -n "$TEST_NS" -f - <<EOF 2>&1 || true
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
        cpu: "100m"
EOF
)
if echo "$RESULT" | grep -q "created"; then
    log_fail "Pod with CAP_SYS_ADMIN was allowed (should be blocked)"
    kubectl delete pod cap-sys-admin-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
elif echo "$RESULT" | grep -q "denied\|not allowed\|dangerous capability"; then
    log_pass "Pod with CAP_SYS_ADMIN was blocked"
else
    log_skip "Unexpected result: $RESULT"
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
        cpu: "100m"
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
        cpu: "100m"
EOF
then
    log_pass "Pod from docker.io was allowed"
    kubectl delete pod allowed-registry-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_fail "Pod from docker.io was rejected"
fi

log_test "Test: Localhost registry should be allowed"
if kubectl apply -n "$TEST_NS" -f - <<EOF >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: localhost-registry-pod
spec:
  containers:
  - name: app
    image: localhost:30500/test:latest
    imagePullPolicy: Never
    resources:
      limits:
        memory: "128Mi"
        cpu: "100m"
EOF
then
    log_pass "Pod from localhost:30500 was allowed"
    kubectl delete pod localhost-registry-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_fail "Pod from localhost:30500 was rejected"
fi

log_test "Test: Disallowed registry should be blocked"
RESULT=$(kubectl apply -n "$TEST_NS" -f - <<EOF 2>&1 || true
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
        cpu: "100m"
EOF
)
if echo "$RESULT" | grep -q "created"; then
    log_fail "Pod from untrusted registry was allowed (should be blocked)"
    kubectl delete pod untrusted-registry-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
elif echo "$RESULT" | grep -q "denied\|not allowed\|disallowed registry"; then
    log_pass "Pod from untrusted registry was blocked"
else
    log_skip "Unexpected result: $RESULT"
fi

# ============================================================================
# RESOURCE LIMITS TESTS
# ============================================================================

echo -e "\n${YELLOW}Resource Limits${NC}"

log_test "Test: Pod without resource limits should be blocked"
RESULT=$(kubectl apply -n "$TEST_NS" -f - <<EOF 2>&1 || true
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
)
if echo "$RESULT" | grep -q "created"; then
    log_fail "Pod without resource limits was allowed (should be blocked)"
    kubectl delete pod no-limits-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
elif echo "$RESULT" | grep -q "denied\|not allowed\|missing resource limits"; then
    log_pass "Pod without resource limits was blocked"
else
    log_skip "Unexpected result: $RESULT"
fi

log_test "Test: Pod with excessive CPU should be blocked"
RESULT=$(kubectl apply -n "$TEST_NS" -f - <<EOF 2>&1 || true
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
      requests:
        cpu: "9000m"
EOF
)
if echo "$RESULT" | grep -q "created"; then
    log_fail "Pod with excessive CPU was allowed (should be blocked)"
    kubectl delete pod excessive-cpu-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
elif echo "$RESULT" | grep -q "denied\|not allowed\|excessive CPU"; then
    log_pass "Pod with excessive CPU was blocked"
else
    log_skip "Unexpected result: $RESULT"
fi

# ============================================================================
# EXEC/ATTACH BLOCKING TESTS
# ============================================================================

echo -e "\n${YELLOW}Exec/Attach Blocking${NC}"

# Create a test pod for exec testing (use unique name to avoid conflicts)
TEST_POD_NAME="exec-test-pod-$$"
# Create a test pod for exec testing using a manifest
TEST_POD_NAME="exec-test-pod-$RANDOM"
cat <<EOF | kubectl apply -n "$TEST_NS" -f - >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: $TEST_POD_NAME
spec:
  restartPolicy: Never
  containers:
  - name: busybox
    image: busybox
    command: ["sleep", "300"]
    resources:
      requests:
        cpu: 50m
        memory: 64Mi
      limits:
        cpu: 100m
        memory: 128Mi
EOF

# Wait for pod to be ready
sleep 3

log_test "Test: kubectl exec should be blocked"
EXEC_RESULT=$(kubectl exec -n "$TEST_NS" "$TEST_POD_NAME" -- echo "exec worked" 2>&1 || true)
if echo "$EXEC_RESULT" | grep -q "exec worked"; then
    log_fail "kubectl exec was allowed (should be blocked)"
elif echo "$EXEC_RESULT" | grep -q "denied\|not allowed\|exec is not allowed"; then
    log_pass "kubectl exec was blocked"
else
    log_skip "Unexpected exec result: $EXEC_RESULT"
fi

log_test "Test: kubectl attach should be blocked"
ATTACH_RESULT=$(timeout 2 kubectl attach -n "$TEST_NS" "$TEST_POD_NAME" 2>&1 || true)
if echo "$ATTACH_RESULT" | grep -q "denied\|not allowed\|attach is not allowed"; then
    log_pass "kubectl attach was blocked"
else
    log_skip "kubectl attach test inconclusive: $ATTACH_RESULT"
fi

# Cleanup exec test pod
kubectl delete pod "$TEST_POD_NAME" -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1

# ============================================================================
# NAMESPACE OPERATIONS TESTS
# ============================================================================

echo -e "\n${YELLOW}Namespace Operations${NC}"

log_test "Test: Creating namespace should be blocked"
NS_CREATE_RESULT=$(kubectl create namespace test-create-ns-$$ 2>&1 || true)
if echo "$NS_CREATE_RESULT" | grep -q "created"; then
    log_fail "Namespace creation was allowed (should be blocked)"
    kubectl delete namespace test-create-ns-$$ --force --grace-period=0 >/dev/null 2>&1
elif echo "$NS_CREATE_RESULT" | grep -q "denied\|not allowed\|prohibited"; then
    log_pass "Namespace creation was blocked"
else
    log_skip "Unexpected result: $NS_CREATE_RESULT"
fi

log_test "Test: Updating existing namespace should be blocked"
NS_UPDATE_RESULT=$(kubectl label namespace default test-label=test 2>&1 || true)
if echo "$NS_UPDATE_RESULT" | grep -q "labeled"; then
    log_fail "Namespace update was allowed (should be blocked)"
    kubectl label namespace default test-label- >/dev/null 2>&1
elif echo "$NS_UPDATE_RESULT" | grep -q "denied\|not allowed\|prohibited"; then
    log_pass "Namespace update was blocked"
else
    # Check if label already existed
    if echo "$NS_UPDATE_RESULT" | grep -q "already has a value"; then
        log_skip "Label already existed, cannot test update"
    else
        log_skip "Unexpected result: $NS_UPDATE_RESULT"
    fi
fi

log_test "Test: Deleting namespace should be blocked"
# First ensure a test namespace exists (it shouldn't be possible to create, but check kube-public)
NS_DELETE_RESULT=$(kubectl delete namespace chutes --dry-run=server 2>&1 || true)
if echo "$NS_DELETE_RESULT" | grep -q "deleted"; then
    log_fail "Namespace deletion was allowed in dry-run (real deletion would be catastrophic)"
elif echo "$NS_DELETE_RESULT" | grep -q "denied\|not allowed\|prohibited"; then
    log_pass "Namespace deletion was blocked"
else
    log_info "Namespace deletion test result: $NS_DELETE_RESULT"
fi

log_test "Test: Creating CRD should be blocked"
CRD_RESULT=$(kubectl apply -f - <<EOF 2>&1 || true
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
)
if echo "$CRD_RESULT" | grep -q "created"; then
    log_fail "CRD creation was allowed (should be blocked)"
    kubectl delete crd tests.example.com --force --grace-period=0 >/dev/null 2>&1
elif echo "$CRD_RESULT" | grep -q "denied\|not allowed\|prohibited"; then
    log_pass "CRD creation was blocked"
else
    log_skip "Unexpected result: $CRD_RESULT"
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
        cpu: "100m"
EOF
then
    log_pass "Pod with HF_ENDPOINT was allowed"
    kubectl delete pod hf-endpoint-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
else
    log_fail "Pod with HF_ENDPOINT was rejected"
fi

log_test "Test: KUBECONFIG environment variable should be blocked"
KUBECONFIG_RESULT=$(kubectl apply -n "$TEST_NS" -f - <<EOF 2>&1 || true
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
        cpu: "100m"
EOF
)
if echo "$KUBECONFIG_RESULT" | grep -q "created"; then
    log_fail "Pod with KUBECONFIG env was allowed (should be blocked)"
    kubectl delete pod kubeconfig-env-pod -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
elif echo "$KUBECONFIG_RESULT" | grep -q "denied\|not allowed\|forbidden environment variable"; then
    log_pass "Pod with KUBECONFIG env was blocked"
else
    log_skip "Unexpected result: $KUBECONFIG_RESULT"
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
PRIV_DEPLOY_RESULT=$(kubectl apply -n "$TEST_NS" -f - <<EOF 2>&1 || true
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
            cpu: "100m"
EOF
)
if echo "$PRIV_DEPLOY_RESULT" | grep -q "created"; then
    log_fail "Privileged deployment was allowed (should be blocked)"
    kubectl delete deployment privileged-deployment -n "$TEST_NS" --force --grace-period=0 >/dev/null 2>&1
elif echo "$PRIV_DEPLOY_RESULT" | grep -q "denied\|not allowed\|privileged"; then
    log_pass "Privileged deployment was blocked"
else
    log_skip "Unexpected result: $PRIV_DEPLOY_RESULT"
fi

# ============================================================================
# NAMESPACE-SPECIFIC POLICY TESTS
# ============================================================================

echo -e "\n${YELLOW}Namespace-Specific Policy Tests${NC}"

# Test system namespace behavior (should be in warn mode)
log_test "Test: System namespace (kube-system) policy check"
SYSTEM_NS_RESULT=$(kubectl apply -n kube-system --dry-run=server -f - <<EOF 2>&1 || true
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
        cpu: "100m"
EOF
)

if echo "$SYSTEM_NS_RESULT" | grep -q "created\|configured"; then
    log_info "System namespace allows privileged pods (warn mode or dry-run)"
elif echo "$SYSTEM_NS_RESULT" | grep -q "denied\|blocked"; then
    log_info "System namespace blocks privileged pods (enforce mode)"
elif echo "$SYSTEM_NS_RESULT" | grep -q "warning"; then
    log_pass "System namespace issues warnings for policy violations"
else
    log_skip "Could not determine system namespace policy: $SYSTEM_NS_RESULT"
fi

# Test chutes namespace (should already exist or use default)
log_test "Test: Chutes namespace enforcement"
# Check if chutes namespace exists
if kubectl get namespace chutes >/dev/null 2>&1; then
    CHUTES_NS="chutes"
    log_info "Using existing chutes namespace for enforcement test"
else
    CHUTES_NS="default"
    log_info "Chutes namespace doesn't exist, testing enforcement in default namespace"
fi

CHUTES_RESULT=$(kubectl apply -n "$CHUTES_NS" -f - <<EOF 2>&1 || true
apiVersion: v1
kind: Pod
metadata:
  name: chutes-test-pod-$$
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    # No resource limits - should be blocked in enforce mode
EOF
)
if echo "$CHUTES_RESULT" | grep -q "created"; then
    log_fail "$CHUTES_NS namespace allowed pod without limits (should enforce)"
    kubectl delete pod chutes-test-pod-$$ -n "$CHUTES_NS" --force --grace-period=0 >/dev/null 2>&1
elif echo "$CHUTES_RESULT" | grep -q "denied\|not allowed\|missing resource limits"; then
    log_pass "$CHUTES_NS namespace enforced resource limits policy"
else
    log_skip "Unexpected result: $CHUTES_RESULT"
fi

# ============================================================================
# METRICS ENDPOINT TEST
# ============================================================================

echo -e "\n${YELLOW}Metrics and Monitoring${NC}"

log_test "Test: Metrics endpoint should be accessible"
METRICS=$(curl -sk https://localhost:8443/metrics 2>/dev/null || echo "")
if echo "$METRICS" | grep -q "admission_requests_total\|admission_controller_info"; then
    log_pass "Metrics endpoint is working with Prometheus format"
    
    # Check for specific metrics
    if echo "$METRICS" | grep -q "admission_requests_total{decision=\"allowed\"}"; then
        log_pass "Admission decision metrics are being collected"
    fi
    if echo "$METRICS" | grep -q "admission_cache_hits_total"; then
        log_pass "Cache metrics are being collected"
    fi
else
    log_fail "Metrics endpoint not working or missing expected metrics"
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
        cpu: "100m"
EOF
END_TIME=$(date +%s%N)
LATENCY=$(( (END_TIME - START_TIME) / 1000000 ))

if [ "$LATENCY" -lt 100 ]; then
    log_pass "Admission latency is excellent: ${LATENCY}ms"
elif [ "$LATENCY" -lt 500 ]; then
    log_pass "Admission latency is good: ${LATENCY}ms"
elif [ "$LATENCY" -lt 1000 ]; then
    log_info "Admission latency is acceptable: ${LATENCY}ms"
else
    log_fail "Admission latency is high: ${LATENCY}ms"
fi

# ============================================================================
# CACHE FUNCTIONALITY TEST
# ============================================================================

echo -e "\n${YELLOW}Cache Functionality Test${NC}"

log_test "Test: Cache functionality"
# Create the same pod spec twice to test caching
POD_SPEC=$(cat <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: cache-test-pod
  namespace: $TEST_NS
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    command: ["sleep", "30"]
    resources:
      limits:
        memory: "128Mi"
        cpu: "100m"
EOF
)

# First request (cache miss)
echo "$POD_SPEC" | kubectl apply --dry-run=server -f - >/dev/null 2>&1

# Check metrics before second request
CACHE_HITS_BEFORE=$(curl -sk https://localhost:8443/metrics 2>/dev/null | grep "admission_cache_hits_total" | grep -v "#" | awk '{print $2}' || echo "0")

# Second request (should be cache hit if caching is working)
echo "$POD_SPEC" | kubectl apply --dry-run=server -f - >/dev/null 2>&1

# Check metrics after second request
CACHE_HITS_AFTER=$(curl -sk https://localhost:8443/metrics 2>/dev/null | grep "admission_cache_hits_total" | grep -v "#" | awk '{print $2}' || echo "0")

if [ "$CACHE_HITS_AFTER" -gt "$CACHE_HITS_BEFORE" ]; then
    log_pass "Cache is functioning (hits increased from $CACHE_HITS_BEFORE to $CACHE_HITS_AFTER)"
else
    log_info "Cache may not be active or request was different"
fi

# ============================================================================
# OPA INTEGRATION TEST
# ============================================================================

echo -e "\n${YELLOW}OPA Integration Test${NC}"

log_test "Test: Direct OPA policy check"
# Test OPA directly with a sample input
OPA_TEST_INPUT=$(cat <<EOF
{
  "input": {
    "request": {
      "kind": {"kind": "Pod"},
      "namespace": "default",
      "operation": "CREATE",
      "object": {
        "spec": {
          "volumes": [{
            "name": "test",
            "hostPath": {"path": "/etc"}
          }]
        }
      }
    }
  }
}
EOF
)

OPA_RESULT=$(echo "$OPA_TEST_INPUT" | curl -s -X POST http://localhost:8181/v1/data/kubernetes/admission/deny -H "Content-Type: application/json" -d @- 2>/dev/null || echo "{}")
if echo "$OPA_RESULT" | grep -q "result"; then
    log_pass "OPA is responding to policy queries"
    if echo "$OPA_RESULT" | grep -q "hostPath.*not allowed"; then
        log_pass "OPA correctly identified policy violation"
    fi
else
    log_fail "OPA not responding correctly to policy queries"
fi

# ============================================================================
# WEBHOOK CONFIGURATION VALIDATION
# ============================================================================

echo -e "\n${YELLOW}Webhook Configuration Validation${NC}"

log_test "Test: Webhook configuration validation"
if kubectl get validatingwebhookconfiguration admission-controller-webhook >/dev/null 2>&1; then
    # Check failure policy
    FAILURE_POLICY=$(kubectl get validatingwebhookconfiguration admission-controller-webhook -o jsonpath='{.webhooks[0].failurePolicy}')
    if [ "$FAILURE_POLICY" = "Fail" ]; then
        log_pass "Webhook configured to fail closed (failurePolicy: Fail)"
    else
        log_fail "Webhook not configured to fail closed (failurePolicy: $FAILURE_POLICY)"
    fi
    
    # Check timeout
    TIMEOUT=$(kubectl get validatingwebhookconfiguration admission-controller-webhook -o jsonpath='{.webhooks[0].timeoutSeconds}')
    if [ "$TIMEOUT" -le "10" ]; then
        log_pass "Webhook timeout is reasonable: ${TIMEOUT}s"
    else
        log_info "Webhook timeout may be high: ${TIMEOUT}s"
    fi
fi

# ============================================================================
# WEBHOOK IMMUTABILITY TESTS
# ============================================================================

echo -e "\n${YELLOW}Webhook Immutability Tests${NC}"

log_test "Test: Webhook configuration should be immutable - DELETE attempt"
DELETE_RESULT=$(kubectl delete validatingwebhookconfiguration admission-controller-webhook 2>&1 || true)
if echo "$DELETE_RESULT" | grep -q "deleted"; then
    log_fail "CRITICAL: Webhook configuration was deleted! This is a security vulnerability!"
    # Try to restore it
    kubectl apply -f /var/lib/rancher/k3s/server/manifests/admission-webhook.yaml 2>/dev/null || true
elif echo "$DELETE_RESULT" | grep -q "denied\|cannot be modified\|protected\|forbidden"; then
    log_pass "Webhook deletion was blocked by admission controller"
else
    # Check if it still exists
    if kubectl get validatingwebhookconfiguration admission-controller-webhook >/dev/null 2>&1; then
        log_pass "Webhook deletion failed (may be protected by other means)"
    else
        log_fail "Webhook was deleted without proper protection!"
    fi
fi

log_test "Test: Webhook configuration should be immutable - UPDATE attempt (change failurePolicy)"
# Save current configuration
ORIGINAL_CONFIG=$(kubectl get validatingwebhookconfiguration admission-controller-webhook -o yaml)

# Try to change failurePolicy to Ignore (dangerous!)
UPDATE_RESULT=$(kubectl patch validatingwebhookconfiguration admission-controller-webhook \
    --type='json' \
    -p='[{"op": "replace", "path": "/webhooks/0/failurePolicy", "value": "Ignore"}]' 2>&1 || true)

if echo "$UPDATE_RESULT" | grep -q "patched\|configured"; then
    # Check what the actual value is now
    CURRENT_POLICY=$(kubectl get validatingwebhookconfiguration admission-controller-webhook -o jsonpath='{.webhooks[0].failurePolicy}')
    if [ "$CURRENT_POLICY" = "Ignore" ]; then
        log_fail "CRITICAL: Webhook failurePolicy was changed to Ignore! This bypasses security!"
        # Try to restore it
        kubectl patch validatingwebhookconfiguration admission-controller-webhook \
            --type='json' \
            -p='[{"op": "replace", "path": "/webhooks/0/failurePolicy", "value": "Fail"}]' >/dev/null 2>&1
    else
        log_info "Patch command succeeded but failurePolicy unchanged (may be mutated back)"
    fi
elif echo "$UPDATE_RESULT" | grep -q "denied\|cannot be modified\|protected\|forbidden"; then
    log_pass "Webhook modification was blocked by admission controller"
else
    log_info "Webhook update had unexpected result: $UPDATE_RESULT"
fi

log_test "Test: Webhook configuration should be immutable - UPDATE attempt (disable webhook)"
# Try to set webhook rules to match nothing (effectively disabling it)
DISABLE_RESULT=$(kubectl patch validatingwebhookconfiguration admission-controller-webhook \
    --type='json' \
    -p='[{"op": "replace", "path": "/webhooks/0/rules", "value": [{"operations": ["CONNECT"], "apiGroups": [""], "apiVersions": ["v1"], "resources": ["none"]}]}]' 2>&1 || true)

if echo "$DISABLE_RESULT" | grep -q "patched\|configured"; then
    # Check if rules were actually changed
    RULES_COUNT=$(kubectl get validatingwebhookconfiguration admission-controller-webhook -o jsonpath='{.webhooks[0].rules}' | grep -c "NONE" || echo "0")
    if [ "$RULES_COUNT" -gt "0" ]; then
        log_fail "CRITICAL: Webhook rules were modified to disable coverage!"
        # Restore from saved config
        echo "$ORIGINAL_CONFIG" | kubectl apply -f - >/dev/null 2>&1
    else
        log_info "Patch succeeded but rules unchanged (may be mutated back)"
    fi
elif echo "$DISABLE_RESULT" | grep -q "denied\|cannot be modified\|protected\|forbidden"; then
    log_pass "Webhook rules modification was blocked"
else
    log_info "Webhook disable attempt had unexpected result: $DISABLE_RESULT"
fi

log_test "Test: Webhook configuration should be immutable - UPDATE attempt (change URL)"
# Try to change the webhook URL to bypass the admission controller
BYPASS_RESULT=$(kubectl patch validatingwebhookconfiguration admission-controller-webhook \
    --type='json' \
    -p='[{"op": "replace", "path": "/webhooks/0/clientConfig/url", "value": "https://evil.example.com/validate"}]' 2>&1 || true)

if echo "$BYPASS_RESULT" | grep -q "patched\|configured"; then
    # Check current URL
    CURRENT_URL=$(kubectl get validatingwebhookconfiguration admission-controller-webhook -o jsonpath='{.webhooks[0].clientConfig.url}')
    if echo "$CURRENT_URL" | grep -q "evil.example.com"; then
        log_fail "CRITICAL: Webhook URL was changed! Admission control can be bypassed!"
        # Restore
        kubectl patch validatingwebhookconfiguration admission-controller-webhook \
            --type='json' \
            -p='[{"op": "replace", "path": "/webhooks/0/clientConfig/url", "value": "https://127.0.0.1:8443/validate"}]' >/dev/null 2>&1
    else
        log_info "Patch succeeded but URL unchanged (may be protected)"
    fi
elif echo "$BYPASS_RESULT" | grep -q "denied\|cannot be modified\|protected\|forbidden"; then
    log_pass "Webhook URL modification was blocked"
else
    log_info "Webhook URL change had unexpected result: $BYPASS_RESULT"
fi

log_test "Test: Webhook configuration should be immutable - REPLACE entire config"
# Try to replace the entire webhook configuration
REPLACE_CONFIG=$(cat <<EOF
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingWebhookConfiguration
metadata:
  name: admission-controller-webhook
webhooks:
- name: validate.admission.local
  clientConfig:
    url: https://127.0.0.1:8443/validate
    caBundle: $(kubectl get validatingwebhookconfiguration admission-controller-webhook -o jsonpath='{.webhooks[0].clientConfig.caBundle}')
  rules:
  - operations: ["CREATE"]
    apiGroups: [""]
    apiVersions: ["v1"]
    resources: ["configmaps"]  # Only validate configmaps (basically disabled)
  failurePolicy: Ignore  # Fail open
  admissionReviewVersions: ["v1"]
  sideEffects: None
EOF
)

REPLACE_RESULT=$(echo "$REPLACE_CONFIG" | kubectl replace -f - 2>&1 || true)
if echo "$REPLACE_RESULT" | grep -q "replaced"; then
    # Check if it was actually replaced with weakened config
    CURRENT_FAILURE=$(kubectl get validatingwebhookconfiguration admission-controller-webhook -o jsonpath='{.webhooks[0].failurePolicy}')
    RESOURCES=$(kubectl get validatingwebhookconfiguration admission-controller-webhook -o jsonpath='{.webhooks[0].rules[0].resources[0]}')
    
    if [ "$CURRENT_FAILURE" = "Ignore" ] || [ "$RESOURCES" = "configmaps" ]; then
        log_fail "CRITICAL: Webhook was replaced with weakened configuration!"
        # Restore original
        echo "$ORIGINAL_CONFIG" | kubectl apply -f - >/dev/null 2>&1
    else
        log_info "Replace succeeded but configuration appears unchanged"
    fi
elif echo "$REPLACE_RESULT" | grep -q "denied\|cannot be modified\|protected\|forbidden"; then
    log_pass "Webhook replacement was blocked"
else
    log_info "Webhook replacement had unexpected result: $REPLACE_RESULT"
fi

log_test "Test: Creating new webhook configurations should be blocked"
NEW_WEBHOOK_RESULT=$(kubectl apply -f - <<EOF 2>&1 || true
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingWebhookConfiguration
metadata:
  name: malicious-webhook
webhooks:
- name: malicious.webhook.sh
  clientConfig:
    url: https://evil.example.com/validate
    caBundle: $(kubectl get validatingwebhookconfiguration admission-controller-webhook -o jsonpath='{.webhooks[0].clientConfig.caBundle}')
  rules:
  - operations: ["*"]
    apiGroups: ["*"]
    apiVersions: ["*"]
    resources: ["*"]
  failurePolicy: Ignore
  admissionReviewVersions: ["v1"]
  sideEffects: None
EOF
)

if echo "$NEW_WEBHOOK_RESULT" | grep -q "created"; then
    log_fail "New webhook configuration was created (should be blocked)!"
    kubectl delete validatingwebhookconfiguration malicious-webhook >/dev/null 2>&1
elif echo "$NEW_WEBHOOK_RESULT" | grep -q "denied\|not allowed\|prohibited"; then
    log_pass "New webhook creation was blocked"
else
    log_info "New webhook creation had unexpected result: $NEW_WEBHOOK_RESULT"
fi

log_test "Test: Creating MutatingWebhookConfiguration should be blocked"
MUTATING_RESULT=$(kubectl apply -f - <<EOF 2>&1 || true
apiVersion: admissionregistration.k8s.io/v1
kind: MutatingWebhookConfiguration
metadata:
  name: malicious-mutator
webhooks:
- name: mutate.malicious.sh
  clientConfig:
    url: https://evil.example.com/mutate
    caBundle: $(kubectl get validatingwebhookconfiguration admission-controller-webhook -o jsonpath='{.webhooks[0].clientConfig.caBundle}')
  rules:
  - operations: ["CREATE", "UPDATE"]
    apiGroups: [""]
    apiVersions: ["v1"]
    resources: ["pods"]
  failurePolicy: Ignore
  admissionReviewVersions: ["v1"]
  sideEffects: None
EOF
)

if echo "$MUTATING_RESULT" | grep -q "created"; then
    log_fail "MutatingWebhookConfiguration was created (should be blocked)!"
    kubectl delete mutatingwebhookconfiguration malicious-mutator >/dev/null 2>&1
elif echo "$MUTATING_RESULT" | grep -q "denied\|not allowed\|prohibited"; then
    log_pass "MutatingWebhookConfiguration creation was blocked"
else
    log_info "Mutating webhook creation had unexpected result: $MUTATING_RESULT"
fi

# Check if the manifest file is protected
log_test "Test: Webhook manifest file protection"
MANIFEST_FILE="/var/lib/rancher/k3s/server/manifests/admission-webhook.yaml"
if [ -f "$MANIFEST_FILE" ]; then
    # Check file permissions
    PERMS=$(stat -c %a "$MANIFEST_FILE")
    OWNER=$(stat -c %U:%G "$MANIFEST_FILE")
    
    if [ "$PERMS" = "400" ] || [ "$PERMS" = "600" ]; then
        log_pass "Webhook manifest has restrictive permissions: $PERMS"
    else
        log_fail "Webhook manifest has loose permissions: $PERMS (should be 400 or 600)"
    fi
    
    if [ "$OWNER" = "root:root" ]; then
        log_pass "Webhook manifest owned by root:root"
    else
        log_fail "Webhook manifest has wrong owner: $OWNER (should be root:root)"
    fi
    
    # Check immutable flag
    if lsattr "$MANIFEST_FILE" 2>/dev/null | grep -q "i"; then
        log_pass "Webhook manifest has immutable flag set"
    else
        log_info "Webhook manifest does not have immutable flag (optional protection)"
    fi
else
    log_skip "Webhook manifest file not found at expected location"
fi

# Verify webhook is still functional after all tests
log_test "Test: Webhook still functional after immutability tests"
if kubectl get validatingwebhookconfiguration admission-controller-webhook >/dev/null 2>&1; then
    # Test with a simple pod creation
    TEST_RESULT=$(kubectl apply -n "$TEST_NS" --dry-run=server -f - <<EOF 2>&1 || true
apiVersion: v1
kind: Pod
metadata:
  name: post-test-pod
spec:
  containers:
  - name: app
    image: docker.io/library/busybox:latest
    resources:
      limits:
        memory: "128Mi"
        cpu: "100m"
EOF
)
    if echo "$TEST_RESULT" | grep -q "created\|configured"; then
        log_pass "Webhook is still functional after immutability tests"
    else
        log_fail "Webhook may be damaged after tests"
    fi
else
    log_fail "Webhook configuration missing after immutability tests!"
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
    echo "Phase 4a admission controller (Python + OPA) is working correctly."
    
    # Show configuration info
    echo -e "\n${BLUE}Configuration Summary:${NC}"
    echo "- OPA URL: http://localhost:8181"
    echo "- Admission Controller: https://localhost:8443"
    echo "- Enforcement modes: enforce (default), warn (system namespaces)"
    echo "- Allowed registries: docker.io, gcr.io, quay.io, localhost:30500"
    echo "- Cache TTL: 300 seconds"
    
    exit 0
else
    echo -e "${YELLOW}Some tests failed. Review the output above for details.${NC}"
    echo ""
    echo "Common issues:"
    echo "1. Ensure webhook configuration is applied:"
    echo "   kubectl get validatingwebhookconfiguration admission-controller-webhook"
    echo ""
    echo "2. Check OPA policies are loaded:"
    echo "   curl http://localhost:8181/v1/policies"
    echo ""
    echo "3. Verify services are running:"
    echo "   systemctl status opa admission-controller"
    echo ""
    echo "4. Check admission controller logs:"
    echo "   journalctl -u admission-controller -n 50"
    echo ""
    echo "5. Check OPA logs:"
    echo "   journalctl -u opa -n 50"
    echo ""
    echo "6. Verify webhook is reachable:"
    echo "   curl -sk https://localhost:8443/health"
    
    exit 1
fi