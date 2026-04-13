package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

# Block API service manipulation
deny contains msg if {
    input.request.kind.kind == "APIService"
    input.request.kind.group == "apiregistration.k8s.io"
    msg := "Creation/modification of APIServices is prohibited"
}
