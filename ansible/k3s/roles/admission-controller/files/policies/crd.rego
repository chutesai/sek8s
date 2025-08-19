package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

import data.helpers

# Block CRD operations that could bypass controls (with K3s exemptions)
deny contains msg if {
    input.request.kind.kind == "CustomResourceDefinition"
    input.request.operation in ["CREATE", "UPDATE", "DELETE"]
    not helpers.is_bootstrap_operation
    not helpers.is_k3s_system_operation
    not helpers.is_k3s_system_crd
    # Allow Gatekeeper CRDs
    not startswith(input.request.name, "gatekeeper")
    not endswith(input.request.name, ".gatekeeper.sh")
    
    msg := sprintf("CRD operation '%s' on '%s' is not allowed", [input.request.operation, input.request.name])
}