package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

# Define sets for operations and kinds
protected_operations := {"UPDATE", "DELETE", "PATCH"}
delete_update_operations := {"UPDATE", "DELETE"}
webhook_kinds := {"ValidatingWebhookConfiguration", "MutatingWebhookConfiguration"}

# Protect the admission webhook configuration itself
deny contains msg if {
    input.request.kind.kind == "ValidatingWebhookConfiguration"
    input.request.name == "admission-controller-webhook"
    protected_operations[input.request.operation]
    msg := "The admission-controller-webhook is protected and cannot be modified"
}

# Prevent disabling of admission plugins via ConfigMap modifications
deny contains msg if {
    input.request.kind.kind == "ConfigMap"
    input.request.namespace == "kube-system"
    input.request.name == "k3s-config"
    delete_update_operations[input.request.operation]
    msg := "K3s configuration cannot be modified at runtime"
}

# Prevent creation of new webhook configurations that might bypass ours
deny contains msg if {
    webhook_kinds[input.request.kind.kind]
    input.request.operation == "CREATE"
    input.request.name != "admission-controller-webhook"
    msg := sprintf("New webhook configurations are not allowed: %s", [input.request.name])
}