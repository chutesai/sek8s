package admission

# Define allowed registries
allowed_registries := input.allowed_registries

# Deny admission if any container uses a disallowed registry
deny contains msg if {
    # Check containers in pod spec
    container := input.request.object.spec.containers[_]
    image := container.image
    registry := get_registry(image)
    not registry in allowed_registries
    
    msg := sprintf("Container image '%s' uses disallowed registry '%s'. Allowed registries: %v", [image, registry, allowed_registries])
}

deny contains msg if {
    # Check init containers in pod spec
    container := input.request.object.spec.initContainers[_]
    image := container.image
    registry := get_registry(image)
    not registry in allowed_registries
    
    msg := sprintf("Init container image '%s' uses disallowed registry '%s'. Allowed registries: %v", [image, registry, allowed_registries])
}

deny contains msg if {
    # Check ephemeral containers in pod spec
    container := input.request.object.spec.ephemeralContainers[_]
    image := container.image
    registry := get_registry(image)
    not registry in allowed_registries
    
    msg := sprintf("Ephemeral container image '%s' uses disallowed registry '%s'. Allowed registries: %v", [image, registry, allowed_registries])
}

deny contains msg if {
    # Check containers in deployment/replicaset/etc template
    container := input.request.object.spec.template.spec.containers[_]
    image := container.image
    registry := get_registry(image)
    not registry in allowed_registries
    
    msg := sprintf("Container image '%s' uses disallowed registry '%s'. Allowed registries: %v", [image, registry, allowed_registries])
}

deny contains msg if {
    # Check init containers in deployment/replicaset/etc template
    container := input.request.object.spec.template.spec.initContainers[_]
    image := container.image
    registry := get_registry(image)
    not registry in allowed_registries
    
    msg := sprintf("Init container image '%s' uses disallowed registry '%s'. Allowed registries: %v", [image, registry, allowed_registries])
}

# Extract registry from image name
get_registry(image) := registry if {
    # Handle images with explicit registry (registry.com/image:tag)
    contains(image, "/")
    parts := split(image, "/")
    
    # Check if first part contains a dot or colon (indicating it's a registry)
    first_part := parts[0]
    registry_indicators := [".", ":"]
    some indicator in registry_indicators
    contains(first_part, indicator)
    registry := first_part
}

get_registry(image) := registry if {
    # Handle images without explicit registry - assume docker.io
    not contains(image, "/")
    registry := "docker.io"
}

get_registry(image) := registry if {
    # Handle docker.io short form (no registry prefix but contains slash)
    contains(image, "/")
    parts := split(image, "/")
    first_part := parts[0]
    
    # If first part doesn't contain registry indicators, it's docker.io
    registry_indicators := [".", ":"]
    count([indicator | indicator := registry_indicators[_]; contains(first_part, indicator)]) == 0
    registry := "docker.io"
}