package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

import data.helpers

# =============================================================================
# SERVICE RESTRICTIONS (chutes namespace)
# =============================================================================

deny contains msg if {
    input.request.kind.kind == "Service"
    input.request.namespace == "chutes"
    not helpers.is_system_or_controller_user
    input.request.object.spec.type == "ExternalName"
    msg := "ExternalName services are not allowed in chutes namespace (traffic redirection risk)"
}

deny contains msg if {
    input.request.kind.kind == "Service"
    input.request.namespace == "chutes"
    input.request.operation in ["CREATE", "UPDATE"]
    not helpers.is_system_or_controller_user
    not input.request.object.metadata.labels["chutes/chute"]
    msg := "Services in chutes namespace must have chutes/chute label"
}
