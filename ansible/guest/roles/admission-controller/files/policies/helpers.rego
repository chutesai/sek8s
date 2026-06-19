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
# Annotation-based bypasses removed: user-settable annotations
# allowed any miner to bypass CRD/webhook restrictions. The admission controller
# is deployed after all other roles in chutes-miner-vm.yml, so build-time operations
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

# GPU operator v26+ bundles Node Feature Discovery (NFD); its upgrade job also updates
# NFD CRDs (nodefeatures.nfd.k8s-sigs.io, nodefeaturerules.nfd.k8s-sigs.io, etc.).
is_gpu_operator_crd_operation if {
    startswith(input.request.userInfo.username, "system:serviceaccount:gpu-operator:")
    endswith(input.request.name, ".nfd.k8s-sigs.io")
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

# True when the request is a rollout restart on a workload controller outside the
# chutes namespace. kubectl rollout restart sets the restartedAt annotation on the
# controller's spec.template.metadata — the pod spec itself does not change.
# We check only that restartedAt changed (not a full spec comparison) to avoid
# fragility from Kubernetes-injected mutations. miner_restart.rego is the
# backstop that ensures the spec cannot actually change for non-system users.
is_rollout_restart if {
    input.request.operation == "UPDATE"
    input.request.namespace != "chutes"
    input.request.kind.kind in ["Deployment", "DaemonSet", "StatefulSet", "ReplicaSet"]
    # Pod template spec must be identical — only the restartedAt annotation may differ.
    # Finalizers live at .metadata.finalizers (top-level object), not inside
    # spec.template.spec, so this comparison is safe against Kubernetes-injected mutations.
    # If spec changed at all this is NOT a rollout restart and security checks apply.
    object.get(input.request.object.spec.template, "spec", {}) ==
        object.get(input.request.oldObject.spec.template, "spec", {})
    # Pod template labels must also be unchanged — a label change would affect which
    # NetworkPolicies and Services apply to the new pods, which is a security concern.
    object.get(input.request.object.spec.template.metadata, "labels", {}) ==
        object.get(input.request.oldObject.spec.template.metadata, "labels", {})
    new_at := object.get(
        object.get(input.request.object.spec.template, "metadata", {}),
        "annotations", {}
    )["kubectl.kubernetes.io/restartedAt"]
    old_at := object.get(
        object.get(
            object.get(input.request.oldObject.spec.template, "metadata", {}),
            "annotations", {}
        ),
        "kubectl.kubernetes.io/restartedAt",
        ""
    )
    new_at != old_at
}