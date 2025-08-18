package kubernetes.admission

import rego.v1

# Default deny
default allow := false

# Allow all requests that are not pod exec
allow if {
    not is_pod_exec
}

# Block all pod exec requests
allow if {
    is_pod_exec
    false  # This will always deny pod exec
}

# Helper rule to identify pod exec requests
is_pod_exec if {
    input.request.kind.kind == "PodExecOptions"
}

is_pod_exec if {
    input.request.kind.kind == "Pod"
    input.request.subResource == "exec"
}

# Violation message for denied requests
violation[msg] if {
    is_pod_exec
    msg := "Pod exec operations are not allowed by policy"
}