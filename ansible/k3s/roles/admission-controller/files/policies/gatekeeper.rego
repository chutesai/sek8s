package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

import data.helpers

# =============================================================================
# GATEKEEPER PROTECTION POLICIES (with K3s system exemptions)
# =============================================================================

# Protect Gatekeeper CRDs from modification
deny contains msg if {
    input.request.kind.group in [
        "templates.gatekeeper.sh",
        "constraints.gatekeeper.sh", 
        "config.gatekeeper.sh"
    ]
    input.request.operation in ["UPDATE", "DELETE"]
    not helpers.is_bootstrap_operation
    not helpers.is_k3s_system_operation
    
    msg := sprintf("Gatekeeper CRD '%s/%s' is protected from modification", [
        input.request.kind.kind, 
        input.request.name
    ])
}

# Protect Gatekeeper namespace resources
deny contains msg if {
    input.request.namespace == "gatekeeper-system"
    input.request.kind.kind in ["Deployment", "Service", "ConfigMap", "Secret", "ServiceAccount"]
    input.request.operation in ["UPDATE", "DELETE"]
    not helpers.is_gatekeeper_internal_operation
    not helpers.is_bootstrap_operation
    not helpers.is_k3s_system_operation
    
    msg := sprintf("Gatekeeper system resource '%s/%s' is protected", [
        input.request.kind.kind,
        input.request.name
    ])
}

# Protect Gatekeeper ValidatingWebhookConfiguration
deny contains msg if {
    input.request.kind.kind == "ValidatingWebhookConfiguration"
    startswith(input.request.name, "gatekeeper-")
    input.request.operation in ["UPDATE", "DELETE"]
    not helpers.is_bootstrap_operation
    not helpers.is_k3s_system_operation
    
    msg := sprintf("Gatekeeper ValidatingWebhookConfiguration '%s' is protected", [input.request.name])
}

# Block creation of bypass annotations/labels unless emergency user
deny contains msg if {
    input.request.operation in ["CREATE", "UPDATE"]
    helpers.has_gatekeeper_bypass_annotation
    not helpers.is_bootstrap_operation
    not helpers.is_k3s_system_operation
    
    msg := "Gatekeeper bypass annotations/labels are not allowed"
}

# Prevent modification of our own webhook configuration (mutual protection)
deny contains msg if {
    input.request.kind.kind == "ValidatingWebhookConfiguration"
    input.request.name == "admission-controller-webhook"
    input.request.operation in ["UPDATE", "DELETE"]
    not helpers.is_bootstrap_operation
    not helpers.is_k3s_system_operation
    
    msg := "External admission controller webhook configuration is protected"
}