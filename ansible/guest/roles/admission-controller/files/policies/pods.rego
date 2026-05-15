package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

import data.helpers

# =============================================================================
# CAPABILITY RESTRICTIONS
# =============================================================================

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    has_dangerous_capability(container)
    msg := sprintf("Container '%s' requests dangerous capability", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.initContainers[_]
    has_dangerous_capability(container)
    msg := sprintf("Init container '%s' requests dangerous capability", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.ephemeralContainers[_]
    has_dangerous_capability(container)
    msg := sprintf("Ephemeral container '%s' requests dangerous capability", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    container := input.request.object.spec.template.spec.containers[_]
    has_dangerous_capability(container)
    msg := sprintf("Container '%s' requests dangerous capability", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    container := input.request.object.spec.template.spec.initContainers[_]
    has_dangerous_capability(container)
    msg := sprintf("Init container '%s' requests dangerous capability", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    container := input.request.object.spec.template.spec.ephemeralContainers[_]
    has_dangerous_capability(container)
    msg := sprintf("Ephemeral container '%s' requests dangerous capability", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.containers[_]
    has_dangerous_capability(container)
    msg := sprintf("Container '%s' requests dangerous capability", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.initContainers[_]
    has_dangerous_capability(container)
    msg := sprintf("Init container '%s' requests dangerous capability", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.ephemeralContainers[_]
    has_dangerous_capability(container)
    msg := sprintf("Ephemeral container '%s' requests dangerous capability", [container.name])
}

# Check for dangerous capabilities
dangerous_capabilities := {
    "SYS_ADMIN",
    "SYS_CHROOT",
    "SYS_MODULE",
    "SYS_RAWIO",
    "SYS_PTRACE",
    "SYS_BOOT",
}

has_dangerous_capability(container) if {
    cap := container.securityContext.capabilities.add[_]
    normalized := trim_prefix(cap, "CAP_")
    normalized in dangerous_capabilities
}


# =============================================================================
# SECURITY CONTEXT RESTRICTIONS
# =============================================================================

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    container.securityContext.privileged == true
    msg := sprintf("Container '%s' has privileged security context", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    input.request.object.spec.hostNetwork == true
    msg := "Pod uses host network which is not allowed"
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    input.request.object.spec.template.spec.hostNetwork == true
    msg := "Template uses host network which is not allowed"
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    input.request.object.spec.jobTemplate.spec.template.spec.hostNetwork == true
    msg := "CronJob template uses host network which is not allowed"
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    input.request.object.spec.hostPID == true
    msg := "Pod uses host PID namespace which is not allowed"
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    input.request.object.spec.template.spec.hostPID == true
    msg := "Template uses host PID namespace which is not allowed"
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    input.request.object.spec.jobTemplate.spec.template.spec.hostPID == true
    msg := "CronJob template uses host PID namespace which is not allowed"
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    input.request.object.spec.hostIPC == true
    msg := "Pod uses host IPC namespace which is not allowed"
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    input.request.object.spec.template.spec.hostIPC == true
    msg := "Template uses host IPC namespace which is not allowed"
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    input.request.object.spec.jobTemplate.spec.template.spec.hostIPC == true
    msg := "CronJob template uses host IPC namespace which is not allowed"
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    container.securityContext.allowPrivilegeEscalation == true
    msg := sprintf("Container '%s' allows privilege escalation", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.initContainers[_]
    container.securityContext.allowPrivilegeEscalation == true
    msg := sprintf("Init container '%s' allows privilege escalation", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.ephemeralContainers[_]
    container.securityContext.allowPrivilegeEscalation == true
    msg := sprintf("Ephemeral container '%s' allows privilege escalation", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    container := input.request.object.spec.template.spec.containers[_]
    container.securityContext.allowPrivilegeEscalation == true
    msg := sprintf("Container '%s' allows privilege escalation", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    container := input.request.object.spec.template.spec.initContainers[_]
    container.securityContext.allowPrivilegeEscalation == true
    msg := sprintf("Init container '%s' allows privilege escalation", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    container := input.request.object.spec.template.spec.ephemeralContainers[_]
    container.securityContext.allowPrivilegeEscalation == true
    msg := sprintf("Ephemeral container '%s' allows privilege escalation", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.containers[_]
    container.securityContext.allowPrivilegeEscalation == true
    msg := sprintf("Container '%s' allows privilege escalation", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.initContainers[_]
    container.securityContext.allowPrivilegeEscalation == true
    msg := sprintf("Init container '%s' allows privilege escalation", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.ephemeralContainers[_]
    container.securityContext.allowPrivilegeEscalation == true
    msg := sprintf("Ephemeral container '%s' allows privilege escalation", [container.name])
}

# Deny pods with privileged containers
deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    # Check containers in pod spec
    not helpers.is_system_or_controller_user
    container := input.request.object.spec.containers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    # Check init containers in pod spec
    not helpers.is_system_or_controller_user
    container := input.request.object.spec.initContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Init container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    # Check ephemeral containers in pod spec
    not helpers.is_system_or_controller_user
    container := input.request.object.spec.ephemeralContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Ephemeral container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    # Check containers in deployment/replicaset/etc template
    not helpers.is_system_or_controller_user
    not only_restartedAt_change
    container := input.request.object.spec.template.spec.containers[_]
    container.securityContext.privileged == true
    msg := sprintf("Container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    # Check init containers in deployment/replicaset/etc template
    not helpers.is_system_or_controller_user
    not only_restartedAt_change
    container := input.request.object.spec.template.spec.initContainers[_]
    container.securityContext.privileged == true
    msg := sprintf("Init container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    # Check ephemeral containers in deployment/replicaset/etc template
    not helpers.is_system_or_controller_user
    not only_restartedAt_change
    container := input.request.object.spec.template.spec.ephemeralContainers[_]
    container.securityContext.privileged == true
    msg := sprintf("Ephemeral container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    not helpers.is_system_or_controller_user
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.containers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    not helpers.is_system_or_controller_user
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.initContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Init container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    not helpers.is_system_or_controller_user
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.ephemeralContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Ephemeral container '%s' has privileged security context which is not allowed", [container.name])
}

# =============================================================================
# RESOURCE LIMITS
# =============================================================================

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    not container.resources.limits
    msg := sprintf("Container '%s' missing resource limits", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    container.resources.limits
    not container.resources.limits.memory
    msg := sprintf("Container '%s' missing memory limit", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    container := input.request.object.spec.template.spec.containers[_]
    not container.resources.limits
    msg := sprintf("Container '%s' missing resource limits", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    container := input.request.object.spec.template.spec.containers[_]
    container.resources.limits
    not container.resources.limits.memory
    msg := sprintf("Container '%s' missing memory limit", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.containers[_]
    not container.resources.limits
    msg := sprintf("Container '%s' missing resource limits", [container.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.containers[_]
    container.resources.limits
    not container.resources.limits.memory
    msg := sprintf("Container '%s' missing memory limit", [container.name])
}

# =============================================================================
# ENVIRONMENT VARIABLE RESTRICTIONS
# =============================================================================

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    env := container.env[_]
    is_forbidden_env_var(env.name)
    msg := sprintf("Container '%s' uses forbidden environment variable '%s'", [container.name, env.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    not only_restartedAt_change

    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"]
    container := input.request.object.spec.template.spec.containers[_]
    env := container.env[_]
    is_forbidden_env_var(env.name)
    msg := sprintf("Container '%s' uses forbidden environment variable '%s'", [container.name, env.name])
}

deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_rollout_restart
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    input.request.kind.kind == "CronJob"
    container := input.request.object.spec.jobTemplate.spec.template.spec.containers[_]
    env := container.env[_]
    is_forbidden_env_var(env.name)
    msg := sprintf("Container '%s' uses forbidden environment variable '%s'", [container.name, env.name])
}

# List of forbidden environment variables (customize as needed)
is_forbidden_env_var(name) if {
    name in [
        "KUBECONFIG",
        "KUBE_TOKEN"
    ]
}

is_forbidden_env_var(name) if {
    not name in allowed_env_vars
}

# Allow certain environment variables that are needed
allowed_env_vars := {
    "HF_TOKEN",
    "CUDA_VISIBLE_DEVICES",
    "NVIDIA_VISIBLE_DEVICES",
    "CHUTES_NVIDIA_DEVICES",
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "TZ",
    "MINER_SEED",
    "MINER_SS58",
    "VALIDATORS",
    "CLUSTER_NAME",
    "CONTROL_PLANE_URL_FILE",
    "CHUTES_EXECUTION_CONTEXT",
    "CHUTES_EXTERNAL_HOST",
    "CHUTES_LAUNCH_JWT",
    "CHUTES_PORT_LOGGING",
    "CHUTES_PORT_PRIMARY",
    "CHUTES_PORT_ATTESTATION",
    "HF_HOME",
    "HF_HUB_DISABLE_XET",
    "HF_HUB_ENABLE_HF_TRANSFER",
    "CIVITAI_HOME",
    "NCCL_DEBUG",
    "NCCL_IB_DISABLE",
    "NCCL_NET_GDR_LEVEL",
    "NCCL_P2P_DISABLE",
    "NCCL_SHM_DISABLE",
    "NCCL_SOCKET_FAMILY",
    "NCCL_SOCKET_IFNAME",
    "VLLM_DISABLE_TELEMETRY",
}

# =============================================================================
# EXEC/ATTACH  RESTRICTIONS
# =============================================================================

# Block ALL exec operations
deny contains msg if {
    input.request.kind.kind == "PodExecOptions"
    not helpers.is_system_or_controller_user
    msg := "Pod exec operations are not permitted."
}

# Block ALL attach operations
deny contains msg if {
    input.request.kind.kind == "PodAttachOptions"
    not helpers.is_system_or_controller_user
    msg := "Pod attach operations are not permitted."
}

# Block ALL port forward operations
deny contains msg if {
    input.request.kind.kind == "PodPortForwardOptions"
    not helpers.is_system_or_controller_user
    msg := "Pod port forward operations are not permitted."
}