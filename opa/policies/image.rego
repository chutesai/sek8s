package admission

# Deny pods with privileged containers
deny contains msg if {
    # Check containers in pod spec
    container := input.request.object.spec.containers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    # Check init containers in pod spec
    container := input.request.object.spec.initContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Init container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    # Check ephemeral containers in pod spec
    container := input.request.object.spec.ephemeralContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Ephemeral container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    # Check containers in deployment/replicaset/etc template
    container := input.request.object.spec.template.spec.containers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    # Check init containers in deployment/replicaset/etc template
    container := input.request.object.spec.template.spec.initContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Init container '%s' has privileged security context which is not allowed", [container.name])
}

deny contains msg if {
    # Check ephemeral containers in deployment/replicaset/etc template
    container := input.request.object.spec.template.spec.ephemeralContainers[_]
    container.securityContext.privileged == true
    
    msg := sprintf("Ephemeral container '%s' has privileged security context which is not allowed", [container.name])
}