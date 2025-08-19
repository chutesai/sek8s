package helpers

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

# Check if operation is from Gatekeeper itself
is_gatekeeper_internal_operation if {
    startswith(input.request.userInfo.username, "system:serviceaccount:gatekeeper-system:")
}

# Check if operation is from K3s system components
is_k3s_system_operation if {
    input.request.userInfo.username in [
        "system:k3s-supervisor",
        "system:k3s-controller",
        "system:k3s",
        "system:apiserver"
    ]
}

# Check if user is a system master
is_system_master if {
    "system:masters" in input.request.userInfo.groups
}

# Check if this is a K3s system CRD
is_k3s_system_crd if {
    input.request.kind.kind == "CustomResourceDefinition"
    endswith(input.request.name, ".k3s.cattle.io")
}

is_k3s_system_crd if {
    input.request.kind.kind == "CustomResourceDefinition"
    endswith(input.request.name, ".cattle.io")
}

# Check for emergency/break-glass user
is_emergency_user if {
    input.request.userInfo.username in [
        "system:admin",
    ]
}

# Check if this is a bootstrap operation (during initial setup)
is_bootstrap_operation if {
    # Check for bootstrap annotation on the request object
    input.request.object.metadata.annotations["admission-controller.tee/bootstrap"] == "true"
}

is_bootstrap_operation if {
    # Allow bootstrap operations from the admission-controller role itself
    input.request.userInfo.username == "system:serviceaccount:kube-system:admission-controller"
}

is_bootstrap_operation if {
    # Allow operations during initial cluster setup (first 30 minutes)
    # This is a fallback for initial deployment scenarios
    cluster_age_annotation := input.request.object.metadata.annotations["admission-controller.tee/cluster-age"]
    cluster_age_annotation == "bootstrap"
}

is_bootstrap_operation if {
    # Allow K3s system operations during bootstrap/restart
    is_k3s_system_operation
    is_k3s_system_crd
}

# Check for Gatekeeper bypass annotations/labels
has_gatekeeper_bypass_annotation if {
    # Check annotations
    annotation_keys := object.get(input.request.object.metadata, "annotations", {})
    annotation_keys["admission.gatekeeper.sh/ignore"]
}

has_gatekeeper_bypass_annotation if {
    # Check labels
    label_keys := object.get(input.request.object.metadata, "labels", {})
    label_keys["admission.gatekeeper.sh/ignore"]
}

# Helper to check if request is for system namespace
is_system_namespace if {
    input.request.namespace in ["kube-system", "kube-public", "kube-node-lease", "gpu-operator"]
}

# Helper to check if this is a pod-creating resource
is_pod_resource if {
    input.request.kind.kind in ["Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "ReplicaSet"]
}