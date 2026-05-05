package helpers

import future.keywords.contains
import future.keywords.if
import future.keywords.in

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

# Check if operation is from K3s system components
is_k3s_system_operation if {
    input.request.userInfo.username in [
        "system:k3s-supervisor",
        "system:k3s-controller",
        "system:k3s",
        "system:apiserver"
    ]
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

# Check if this is a bootstrap operation (during initial setup).
# Annotation-based bypasses removed (SEK8S-009): user-settable annotations
# allowed any miner to bypass CRD/webhook restrictions. The admission controller
# is deployed after all other roles in site.yml, so build-time operations
# complete before policies are active.
is_bootstrap_operation if {
    input.request.userInfo.username == "system:serviceaccount:kube-system:admission-controller"
}

is_bootstrap_operation if {
    is_k3s_system_operation
    is_k3s_system_crd
}

# Helper to check if this is a pod-creating resource
is_pod_resource if {
    input.request.kind.kind in ["Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "ReplicaSet"]
}

# True when the request is from a system/controller that manages cluster resources.
# These are exempt from miner_restart restriction so addon sync, operators, etc. can update Deployments/DaemonSets.
is_system_or_controller_user if {
    input.request.userInfo.username in [
        "system:k3s-supervisor",
        "system:k3s-controller",
        "system:k3s",
        "system:apiserver"
    ]
}

is_system_or_controller_user if {
    startswith(input.request.userInfo.username, "system:serviceaccount:kube-system:")
}

is_system_or_controller_user if {
    startswith(input.request.userInfo.username, "system:serviceaccount:gpu-operator:")
}

# GPU operator service accounts managing NVIDIA CRDs (e.g. gpu-operator-upgrade-crd job).
is_gpu_operator_crd_operation if {
    startswith(input.request.userInfo.username, "system:serviceaccount:gpu-operator:")
    endswith(input.request.name, ".nvidia.com")
}

is_system_or_controller_user if {
    startswith(input.request.userInfo.username, "system:serviceaccount:attestation-system:")
}

is_system_or_controller_user if {
    startswith(input.request.userInfo.username, "system:serviceaccount:monitoring:")
}

is_system_or_controller_user if {
    "system:masters" in input.request.userInfo.groups
}