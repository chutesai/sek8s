package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

import data.helpers

# Define sets for operations and kinds
protected_operations := {"UPDATE", "DELETE", "PATCH"}
delete_update_operations := {"UPDATE", "DELETE"}
webhook_kinds := {"ValidatingWebhookConfiguration", "MutatingWebhookConfiguration"}

# =============================================================================
# ADMISSION WEBHOOK MANIPULATION
# =============================================================================

deny contains msg if {
    input.request.kind.kind in ["ValidatingWebhookConfiguration", "MutatingWebhookConfiguration"]
    input.request.operation in ["CREATE", "UPDATE", "DELETE"]
    msg := sprintf("Modifying admission webhooks is prohibited: %s", [input.request.kind.kind])
}

# Block creation of new admission webhooks that could bypass controls
deny contains msg if {
    input.request.kind.kind in ["ValidatingWebhookConfiguration", "MutatingWebhookConfiguration"]
    input.request.operation == "CREATE"
    input.request.name not in [
        "admission-controller-webhook",
        "gatekeeper-validating-webhook-configuration",
        "gatekeeper-mutating-webhook-configuration"
    ]
    not helpers.is_bootstrap_operation
    not helpers.is_k3s_system_operation
    
    msg := sprintf("Creation of new admission webhooks is not allowed: %s", [input.request.name])
}

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

# Block webhook configuration that could bypass controls
deny contains msg if {
    input.request.kind.kind in ["ValidatingAdmissionWebhook", "MutatingAdmissionWebhook"]
    webhook := input.request.object.webhooks[_]
    webhook.failurePolicy == "Ignore"
    msg := "Admission webhooks must have failurePolicy: Fail"
}