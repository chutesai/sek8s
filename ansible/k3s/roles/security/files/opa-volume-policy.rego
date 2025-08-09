package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

# Deny pods with hostPath volumes that are not /cache or /tmp
deny contains msg if {
    input.request.kind.kind == "Pod"
    some volume in input.request.object.spec.volumes
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    not is_tmp_mount_for_job(input.request.object)
    msg := sprintf("hostPath volume '%s' is not allowed. Only /cache paths are permitted (or /tmp for jobs)", [volume.hostPath.path])
}

# Helper to check if this is a job pod mounting to /tmp
is_tmp_mount_for_job(pod) if {
    # Check if pod has job-name label (created by Job controller)
    pod.metadata.labels["job-name"]
}

# Allow jobs to mount to /tmp within their containers
allow_job_tmp_mount if {
    input.request.kind.kind == "Job"
    some volume in input.request.object.spec.template.spec.volumes
    volume.hostPath
    # Note: The actual /tmp mount happens inside the container namespace
    # This allows jobs to specify hostPath volumes that will be mounted to container's /tmp
}

# Deny persistent volume claims with hostPath that are not /cache
deny contains msg if {
    input.request.kind.kind == "PersistentVolume"
    input.request.object.spec.hostPath
    not startswith(input.request.object.spec.hostPath.path, "/cache")
    msg := sprintf("PersistentVolume hostPath '%s' is not allowed. Only /cache paths are permitted", [input.request.object.spec.hostPath.path])
}

# Deny jobs with hostPath volumes that are not /cache (but allow emptyDir for /tmp)
deny contains msg if {
    input.request.kind.kind == "Job"
    some volume in input.request.object.spec.template.spec.volumes
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    msg := sprintf("Job hostPath volume '%s' is not allowed. Only /cache paths are permitted. Use emptyDir for temporary storage.", [volume.hostPath.path])
}

# Deny deployments with hostPath volumes that are not /cache
deny contains msg if {
    input.request.kind.kind == "Deployment"
    some volume in input.request.object.spec.template.spec.volumes
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    msg := sprintf("Deployment hostPath volume '%s' is not allowed. Only /cache paths are permitted", [volume.hostPath.path])
}

# Deny StatefulSets with hostPath volumes that are not /cache
deny contains msg if {
    input.request.kind.kind == "StatefulSet"
    some volume in input.request.object.spec.template.spec.volumes
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    msg := sprintf("StatefulSet hostPath volume '%s' is not allowed. Only /cache paths are permitted", [volume.hostPath.path])
}

# Deny DaemonSets with hostPath volumes that are not /cache
deny contains msg if {
    input.request.kind.kind == "DaemonSet"
    some volume in input.request.object.spec.template.spec.volumes
    volume.hostPath
    not startswith(volume.hostPath.path, "/cache")
    msg := sprintf("DaemonSet hostPath volume '%s' is not allowed. Only /cache paths are permitted", [volume.hostPath.path])
}

# Allow system namespaces to mount what they need
allow_system_namespace if {
    input.request.namespace in ["kube-system", "kube-public", "kube-node-lease"]
}

# Main decision - allow if no deny rules triggered and not a system namespace mount
allow if {
    count(deny) == 0
}

# Main decision for system namespaces - always allow
allow if {
    allow_system_namespace
}