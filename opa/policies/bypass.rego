# /etc/admission-policies/bypass-prevention.rego
package admission

# Completely block CRD creation
deny contains msg if {
    input.request.kind.kind == "CustomResourceDefinition"
    input.request.kind.group == "apiextensions.k8s.io"
    msg := "Creation of CustomResourceDefinitions is prohibited for security"
}

# Block admission webhook manipulation
deny contains msg if {
    input.request.kind.kind in ["ValidatingAdmissionWebhook", "MutatingAdmissionWebhook"]
    input.request.kind.group == "admissionregistration.k8s.io"
    msg := sprintf("Modification of admission webhooks is prohibited: %s", [input.request.kind.kind])
}

# Block API service manipulation
deny contains msg if {
    input.request.kind.kind == "APIService"
    input.request.kind.group == "apiregistration.k8s.io"
    msg := "Creation/modification of APIServices is prohibited"
}

# Block webhook configuration that could bypass controls
deny contains msg if {
    input.request.kind.kind in ["ValidatingAdmissionWebhook", "MutatingAdmissionWebhook"]
    webhook := input.request.object.webhooks[_]
    webhook.failurePolicy == "Ignore"
    msg := "Admission webhooks must have failurePolicy: Fail"
}
