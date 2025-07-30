package admission

# Block all namespace operations
deny contains msg if {
    input.request.kind.kind == "Namespace"
    input.request.kind.group == ""  # Core API group
    input.request.operation in ["CREATE", "UPDATE", "DELETE"]
    
    msg := sprintf("Namespace %s operations are prohibited", [input.request.operation])
}

# Block namespace creation via any API version
deny contains msg if {
    input.request.kind.kind == "Namespace"
    input.request.kind.version == "v1"
    input.request.operation == "CREATE"
    
    msg := "Creation of new namespaces is prohibited"
}

# Block namespace updates/patches
deny contains msg if {
    input.request.kind.kind == "Namespace"
    input.request.operation in ["UPDATE", "PATCH"]
    
    msg := sprintf("Namespace modifications (%s) are prohibited", [input.request.operation])
}

# Block namespace deletion
deny contains msg if {
    input.request.kind.kind == "Namespace"
    input.request.operation == "DELETE"
    
    msg := "Namespace deletion is prohibited"
}