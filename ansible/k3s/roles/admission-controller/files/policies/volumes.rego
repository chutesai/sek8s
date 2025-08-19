package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

# =============================================================================
# VOLUME MOUNT RESTRICTIONS
# =============================================================================

deny contains msg if {
    helpers.is_pod_resource
    not is_system_namespace
    
    # Check Pod directly
    input.request.kind.kind == "Pod"
    volume := input.request.object.spec.volumes[_]
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    not is_tmp_mount_for_job(input.request.object)
    msg := sprintf("hostPath volume '%s' not allowed. Only /cache paths are permitted", [volume.hostPath.path])
}

deny contains msg if {
    helpers.is_pod_resource
    not is_system_namespace
    
    # Check Deployment/StatefulSet/DaemonSet templates
    input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
    volume := input.request.object.spec.template.spec.volumes[_]
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    msg := sprintf("hostPath volume '%s' not allowed. Only /cache paths are permitted", [volume.hostPath.path])
}

deny contains msg if {
    helpers.is_pod_resource
    not is_system_namespace
    
    # Check Job templates
    input.request.kind.kind == "Job"
    volume := input.request.object.spec.template.spec.volumes[_]
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    msg := sprintf("Job hostPath volume '%s' not allowed. Only /cache paths are permitted. Use emptyDir for temporary storage.", [volume.hostPath.path])
}

deny contains msg if {
    helpers.is_pod_resource
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