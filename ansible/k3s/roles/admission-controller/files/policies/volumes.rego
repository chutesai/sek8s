package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

import data.helpers

# =============================================================================
# VOLUME MOUNT RESTRICTIONS
# =============================================================================

deny contains msg if {
    input.request.operation == "CREATE"
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    # Check Pod directly
    input.request.kind.kind == "Pod"
    volume := input.request.object.spec.volumes[_]
    volume.hostPath
    not is_allowed_hostpath(volume.hostPath.path, input.request.object)
    msg := sprintf("hostPath volume '%s' not allowed.", [volume.hostPath.path])
}

deny contains msg if {
    input.request.operation == "CREATE"
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    # Check Deployment/StatefulSet/DaemonSet templates
    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
    volume := input.request.object.spec.template.spec.volumes[_]
    volume.hostPath
    not is_allowed_hostpath(volume.hostPath.path, input.request.object)
    msg := sprintf("hostPath volume '%s' not allowed.", [volume.hostPath.path])
}

deny contains msg if {
    input.request.operation == "CREATE"
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    # Check Job templates
    input.request.kind.kind == "Job"
    volume := input.request.object.spec.template.spec.volumes[_]
    volume.hostPath
    not is_allowed_hostpath(volume.hostPath.path, input.request.object)
    msg := sprintf("Job hostPath volume '%s' not allowed. Use emptyDir for temporary storage.", [volume.hostPath.path])
}

deny contains msg if {
    input.request.operation == "CREATE"
    helpers.is_pod_resource
    not helpers.is_system_or_controller_user
    
    # Check CronJob templates
    input.request.kind.kind == "CronJob"
    volume := input.request.object.spec.jobTemplate.spec.template.spec.volumes[_]
    volume.hostPath
    not is_allowed_hostpath(volume.hostPath.path, input.request.object)
    msg := sprintf("CronJob hostPath volume '%s' not allowed.", [volume.hostPath.path])
}

# =============================================================================
# HOSTPATH ALLOWLIST HELPERS
# =============================================================================

# Cache path: /var/snap/cache exactly or /var/snap/cache/... (not /var/snap/cache-evil)
is_cache_hostpath(path) if { path == "/var/snap/cache" }
is_cache_hostpath(path) if { startswith(path, "/var/snap/cache/") }

# Image from the validator registry (cosign-verified chute images).
# data.config.validator_registry is set at deploy time via config.json
# (e.g. "myvalidator.localregistry.chutes.ai:30500").
has_validator_registry_image(pod_spec) if {
    container := pod_spec.containers[_]
    startswith(container.image, data.config.validator_registry)
}

# Image from parachutes/chutes-agent (cosign-verified via parachutes org key)
has_agent_image(pod_spec) if {
    container := pod_spec.containers[_]
    startswith(container.image, "parachutes/chutes-agent")
}

# =============================================================================
# CACHE HOSTPATH: chute label + validator registry image + chutes namespace
# =============================================================================

# Pod: chute label + validator registry image
is_allowed_hostpath(path, obj) if {
    is_cache_hostpath(path)
    input.request.namespace == "chutes"
    obj.metadata.labels["chutes/chute"] == "true"
    has_validator_registry_image(obj.spec)
}

# Job/Deployment/etc: chute label on template + validator registry image
is_allowed_hostpath(path, obj) if {
    is_cache_hostpath(path)
    input.request.namespace == "chutes"
    obj.spec.template.metadata.labels["chutes/chute"] == "true"
    has_validator_registry_image(obj.spec.template.spec)
}

# =============================================================================
# AGENT HOSTPATH: agent label + parachutes/chutes-agent image + chutes namespace
# =============================================================================

# Pod
is_allowed_hostpath(path, obj) if {
    path == "/var/lib/chutes/agent"
    input.request.namespace == "chutes"
    obj.metadata.labels["app.kubernetes.io/name"] == "agent"
    has_agent_image(obj.spec)
}

# Deployment/etc: agent label on template
is_allowed_hostpath(path, obj) if {
    path == "/var/lib/chutes/agent"
    input.request.namespace == "chutes"
    obj.spec.template.metadata.labels["app.kubernetes.io/name"] == "agent"
    has_agent_image(obj.spec.template.spec)
}