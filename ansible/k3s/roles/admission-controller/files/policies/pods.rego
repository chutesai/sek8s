package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

import data.helpers

# =============================================================================
# CAPABILITY RESTRICTIONS
# =============================================================================

deny contains msg if {
    helpers.is_pod_resource
    not is_system_namespace
    
    # Check containers in Pod
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    has_dangerous_capability(container)
    msg := sprintf("Container '%s' requests dangerous capability", [container.name])
}

deny contains msg if {
    helpers.is_pod_resource
    not is_system_namespace
    
    # Check containers in templates
    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
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

deny contains msg if {
    helpers.is_pod_resource
    not is_system_namespace
    
    # Check for privileged containers
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    container.securityContext.privileged == true
    msg := sprintf("Container '%s' has privileged security context", [container.name])
}

deny contains msg if {
    helpers.is_pod_resource
    not is_system_namespace
    
    # Check for host network
    input.request.kind.kind == "Pod"
    input.request.object.spec.hostNetwork == true
    msg := "Pod uses host network which is not allowed"
}

deny contains msg if {
    helpers.is_pod_resource
    not is_system_namespace
    
    # Check for host PID
    input.request.kind.kind == "Pod"
    input.request.object.spec.hostPID == true
    msg := "Pod uses host PID namespace which is not allowed"
}

deny contains msg if {
    helpers.is_pod_resource
    not is_system_namespace
    
    # Check for host IPC
    input.request.kind.kind == "Pod"
    input.request.object.spec.hostIPC == true
    msg := "Pod uses host IPC namespace which is not allowed"
}

deny contains msg if {
    helpers.is_pod_resource
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
# REGISTRY RESTRICTIONS (redundant with Python validator but good for defense in depth)
# =============================================================================

deny contains msg if {
    helpers.is_pod_resource
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

deny contains msg if {
    helpers.is_pod_resource
    not is_system_namespace
    
    # Check for missing resource limits
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    not container.resources.limits
    msg := sprintf("Container '%s' missing resource limits", [container.name])
}

deny contains msg if {
    helpers.is_pod_resource
    not is_system_namespace
    
    # Check for missing memory limits specifically
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    container.resources.limits
    not container.resources.limits.memory
    msg := sprintf("Container '%s' missing memory limit", [container.name])
}

deny contains msg if {
    helpers.is_pod_resource
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

deny contains msg if {
    helpers.is_pod_resource
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