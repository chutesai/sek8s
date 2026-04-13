package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

# =============================================================================
# NAMESPACE OPERATIONS
# =============================================================================

# Block all namespace operations
deny contains msg if {
    input.request.kind.kind == "Namespace"
    input.request.kind.group == ""  # Core API group
    input.request.operation in ["CREATE", "UPDATE", "DELETE"]
    
    msg := sprintf("Namespace %s operations are prohibited", [input.request.operation])
}