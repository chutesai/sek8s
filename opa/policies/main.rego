# /etc/opa/policies/main.rego
package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

# Main deny rule that aggregates all policy violations
deny contains msg if {
    msg := volume_violations[_]
}

deny contains msg if {
    msg := capability_violations[_]
}

deny contains msg if {
    msg := security_context_violations[_]
}

deny contains msg if {
    msg := exec_violations[_]
}

deny contains msg if {
    msg := registry_violations[_]
}

deny contains msg if {
    msg := resource_violations[_]
}

deny contains msg if {
    msg := env_var_violations[_]
}

# Helper to check if request is for system namespace
is_system_namespace if {
    input.request.namespace in ["kube-system", "kube-public", "kube-node-lease", "gpu-operator"]
}

# Helper to check if this is a pod-creating resource
is_pod_resource if {
    input.request.kind.kind in ["Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "ReplicaSet"]
}


# =============================================================================
# VOLUME MOUNT RESTRICTIONS
# =============================================================================

volume_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check Pod directly
    input.request.kind.kind == "Pod"
    volume := input.request.object.spec.volumes[_]
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    not is_tmp_mount_for_job(input.request.object)
    msg := sprintf("hostPath volume '%s' not allowed. Only /cache paths are permitted", [volume.hostPath.path])
}

volume_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check Deployment/StatefulSet/DaemonSet templates
    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
    volume := input.request.object.spec.template.spec.volumes[_]
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    msg := sprintf("hostPath volume '%s' not allowed. Only /cache paths are permitted", [volume.hostPath.path])
}

volume_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check Job templates
    input.request.kind.kind == "Job"
    volume := input.request.object.spec.template.spec.volumes[_]
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    msg := sprintf("Job hostPath volume '%s' not allowed. Only /cache paths are permitted. Use emptyDir for temporary storage.", [volume.hostPath.path])
}

volume_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check CronJob templates
    input.request.kind.kind == "CronJob"
    volume := input.request.object.spec.jobTemplate.spec.template.spec.volumes[_]
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    msg := sprintf("CronJob hostPath volume '%s' not allowed. Only /cache paths are permitted", [volume.hostPath.path])
}

# Helper to check if this is a job that needs /tmp
is_tmp_mount_for_job(pod) if {
    pod.metadata.labels["job-name"]
}


# =============================================================================
# CAPABILITY RESTRICTIONS
# =============================================================================

capability_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check containers in Pod
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    has_dangerous_capability(container)
    msg := sprintf("Container '%s' requests dangerous capability", [container.name])
}

capability_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check containers in templates
    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
    container := input.request.object.spec.template.spec.containers[_]
    has_dangerous_capability(container)
    msg := sprintf("Container '%s' requests dangerous capability", [container.name])
}

capability_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check containers in Job templates
    input.request.kind.kind == "Job"
    container := input.request.object.spec.template.spec.containers[_]
    has_dangerous_capability(container)
    msg := sprintf("Container '%s' requests dangerous capability", [container.name])
}

# Check for dangerous capabilities
has_dangerous_capability(container) if {
    container.securityContext.capabilities.add[_] in [
        "CAP_SYS_ADMIN",
        "CAP_SYS_CHROOT", 
        "CAP_SYS_MODULE",
        "CAP_SYS_RAWIO",
        "CAP_SYS_PTRACE",
        "CAP_SYS_BOOT"
    ]
}


# =============================================================================
# SECURITY CONTEXT RESTRICTIONS
# =============================================================================

security_context_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check for privileged containers
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    container.securityContext.privileged == true
    msg := sprintf("Container '%s' has privileged security context", [container.name])
}

security_context_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check for host network
    input.request.kind.kind == "Pod"
    input.request.object.spec.hostNetwork == true
    msg := "Pod uses host network which is not allowed"
}

security_context_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check for host PID
    input.request.kind.kind == "Pod"
    input.request.object.spec.hostPID == true
    msg := "Pod uses host PID namespace which is not allowed"
}

security_context_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check for host IPC
    input.request.kind.kind == "Pod"
    input.request.object.spec.hostIPC == true
    msg := "Pod uses host IPC namespace which is not allowed"
}

security_context_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check for privilege escalation in templates
    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
    container := input.request.object.spec.template.spec.containers[_]
    container.securityContext.allowPrivilegeEscalation == true
    msg := sprintf("Container '%s' allows privilege escalation", [container.name])
}

# Deny pods with privileged containers
deny contains msg if {
    # Check containers in pod spec
    container := input.request.object.spec.containers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    # Check init containers in pod spec
    container := input.request.object.spec.initContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Init container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    # Check ephemeral containers in pod spec
    container := input.request.object.spec.ephemeralContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Ephemeral container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    # Check containers in deployment/replicaset/etc template
    container := input.request.object.spec.template.spec.containers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    # Check init containers in deployment/replicaset/etc template
    container := input.request.object.spec.template.spec.initContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Init container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    # Check ephemeral containers in deployment/replicaset/etc template
    container := input.request.object.spec.template.spec.ephemeralContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Ephemeral container '%s' has privileged security context which is not allowed", [container.name])
}


# =============================================================================
# EXEC/ATTACH RESTRICTIONS
# =============================================================================

exec_violations contains msg if {
    # Block all exec requests
    input.request.operation in ["CONNECT"]
    input.request.subResource == "exec"
    msg := "kubectl exec is not allowed for security reasons"
}

exec_violations contains msg if {
    # Block attach requests
    input.request.operation in ["CONNECT"]
    input.request.subResource == "attach"
    msg := "kubectl attach is not allowed for security reasons"
}


# =============================================================================
# REGISTRY RESTRICTIONS (redundant with Python validator but good for defense in depth)
# =============================================================================

registry_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check images in Pod containers
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    not is_allowed_registry(container.image)
    msg := sprintf("Container image '%s' from disallowed registry", [container.image])
}

# Check if image is from allowed registry
is_allowed_registry(image) if {
    registry := extract_registry(image)
    registry in input.allowed_registries
}

# Default allow for images without registry (docker.io)
is_allowed_registry(image) if {
    not contains(image, "/")
    "docker.io" in input.allowed_registries
}

# Extract registry from image
extract_registry(image) := registry if {
    contains(image, "/")
    parts := split(image, "/")
    contains(parts[0], ".")
    registry := parts[0]
}

extract_registry(image) := registry if {
    contains(image, "/")
    parts := split(image, "/")
    contains(parts[0], ":")
    registry := parts[0]
}

extract_registry(image) := "docker.io" if {
    not contains(image, "/")
}

extract_registry(image) := "docker.io" if {
    contains(image, "/")
    parts := split(image, "/")
    not contains(parts[0], ".")
    not contains(parts[0], ":")
}


# =============================================================================
# RESOURCE LIMITS
# =============================================================================

resource_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check for missing resource limits
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    not container.resources.limits
    msg := sprintf("Container '%s' missing resource limits", [container.name])
}

resource_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check for missing memory limits specifically
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    container.resources.limits
    not container.resources.limits.memory
    msg := sprintf("Container '%s' missing memory limit", [container.name])
}

resource_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check for excessive CPU requests
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    container.resources.requests.cpu
    cpu_value := parse_cpu(container.resources.requests.cpu)
    cpu_value > 8000  # 8 CPUs in millicores
    msg := sprintf("Container '%s' requests excessive CPU: %s", [container.name, container.resources.requests.cpu])
}

# Helper to parse CPU values (simplified)
parse_cpu(cpu_str) := value if {
    endswith(cpu_str, "m")
    value := to_number(trim_suffix(cpu_str, "m"))
}

parse_cpu(cpu_str) := value if {
    not endswith(cpu_str, "m")
    value := to_number(cpu_str) * 1000
}


# =============================================================================
# ENVIRONMENT VARIABLE RESTRICTIONS
# =============================================================================

env_var_violations contains msg if {
    is_pod_resource
    not is_system_namespace
    
    # Check for forbidden environment variables
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    env := container.env[_]
    is_forbidden_env_var(env.name)
    msg := sprintf("Container '%s' uses forbidden environment variable '%s'", [container.name, env.name])
}

# List of forbidden environment variables (customize as needed)
is_forbidden_env_var(name) if {
    startswith(name, "KUBERNETES_")
    not name in [
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT"
    ]
}

is_forbidden_env_var(name) if {
    name in [
        "KUBECONFIG",
        "KUBE_TOKEN"
    ]
}

# Allow certain environment variables that are needed
allowed_env_vars := {
    "HF_ENDPOINT",
    "HF_TOKEN",
    "CUDA_VISIBLE_DEVICES",
    "NVIDIA_VISIBLE_DEVICES",
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "TZ"
}


# =============================================================================
# NAMESPACE OPERATIONS
# =============================================================================

# Block all namespace operations
deny contains msg if {
    input.request.kind.kind == "Namespace"
    input.request.kind.group == ""  # Core API group
    input.request.operation in ["CREATE", "UPDATE", "DELETE"]
    
    msg := sprintf("Namespace %s operations are prohibited", [input.request.operation])
}

# Block namespace creation via any API version
deny contains msg if {
    input.request.kind.kind == "Namespace"
    input.request.kind.version == "v1"
    input.request.operation == "CREATE"
    
    msg := "Creation of new namespaces is prohibited"
}

# Block namespace updates/patches
deny contains msg if {
    input.request.kind.kind == "Namespace"
    input.request.operation in ["UPDATE", "PATCH"]
    
    msg := sprintf("Namespace modifications (%s) are prohibited", [input.request.operation])
}

# Block namespace deletion
deny contains msg if {
    input.request.kind.kind == "Namespace"
    input.request.operation == "DELETE"
    
    msg := "Namespace deletion is prohibited"
}

# =============================================================================
# CUSTOM RESOURCE DEFINITIONS
# =============================================================================

deny contains msg if {
    input.request.kind.kind == "CustomResourceDefinition"
    input.request.operation == "CREATE"
    msg := "Creating CustomResourceDefinitions is prohibited"
}


# =============================================================================
# ADMISSION WEBHOOK MANIPULATION
# =============================================================================

deny contains msg if {
    input.request.kind.kind in ["ValidatingWebhookConfiguration", "MutatingWebhookConfiguration"]
    input.request.operation in ["CREATE", "UPDATE", "DELETE"]
    msg := sprintf("Modifying admission webhooks is prohibited: %s", [input.request.kind.kind])
}

# =============================================================================
# BYPASS RESTRICTIONS
# =============================================================================

# Completely block CRD creation
deny contains msg if {
    input.request.kind.kind == "CustomResourceDefinition"
    input.request.kind.group == "apiextensions.k8s.io"
    msg := "Creation of CustomResourceDefinitions is prohibited for security"
}

# Block admission webhook manipulation
deny contains msg if {
    input.request.kind.kind in ["ValidatingAdmissionWebhook", "MutatingAdmissionWebhook"]
    input.request.kind.group == "admissionregistration.k8s.io"
    msg := sprintf("Modification of admission webhooks is prohibited: %s", [input.request.kind.kind])
}

# Block API service manipulation
deny contains msg if {
    input.request.kind.kind == "APIService"
    input.request.kind.group == "apiregistration.k8s.io"
    msg := "Creation/modification of APIServices is prohibited"
}

# Block webhook configuration that could bypass controls
deny contains msg if {
    input.request.kind.kind in ["ValidatingAdmissionWebhook", "MutatingAdmissionWebhook"]
    webhook := input.request.object.webhooks[_]
    webhook.failurePolicy == "Ignore"
    msg := "Admission webhooks must have failurePolicy: Fail"
}