package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

# Deny all RBAC modifications after initial setup
deny contains msg if {
    input.request.kind.group == "rbac.authorization.k8s.io"
    input.request.operation in ["CREATE", "UPDATE", "DELETE"]
    not is_initial_setup
    msg := sprintf("RBAC modifications are locked down. Operation %s on %s/%s is denied", 
                   [input.request.operation, input.request.kind.kind, input.request.name])
}

# Deny modifications to admission webhooks
deny contains msg if {
    input.request.kind.group == "admissionregistration.k8s.io"
    input.request.operation in ["CREATE", "UPDATE", "DELETE"]
    not is_initial_setup
    msg := sprintf("Admission webhook modifications are locked down. Operation %s on %s is denied", 
                   [input.request.operation, input.request.kind.kind])
}

# Deny modifications to the admission controller webhook specifically
deny contains msg if {
    input.request.kind.kind == "ValidatingWebhookConfiguration"
    input.request.name == "admission-controller-webhook"
    input.request.operation in ["UPDATE", "DELETE"]
    msg := "The admission-controller-webhook cannot be modified or deleted"
}

# Check if this is initial setup (could be based on a ConfigMap flag or time window)
is_initial_setup if {
    # Option 1: Check for a setup flag in kube-system
    # data.kubernetes.configmaps["kube-system"]["cluster-setup-complete"] != "true"
    
    # Option 2: Always false after deployment
    false
}