# Outside chutes namespace: only allow rollout restart (restartedAt-only patch).
# Chutes namespace: miner RBAC allows full patches; no restriction here.
# System/controller users are exempt so operators can reconcile their own resources.
package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

import data.helpers

# Deny UPDATE on Deployment/DaemonSet in non-chutes namespaces when patch changes more than restartedAt
deny contains msg if {
	input.request.namespace != "chutes"
	input.request.operation == "UPDATE"
	input.request.kind.kind in ["Deployment", "DaemonSet"]
	not helpers.is_system_or_controller_user
	not only_restartedAt_change
	msg := "Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"
}

req_object := input.request.object
oldObject := input.request.oldObject

# RestartedAt-only: security-relevant sections of the object must be unchanged.
# We compare selector, template spec, template labels, and annotations (minus
# restartedAt). Kubernetes-managed metadata (creationTimestamp, resourceVersion,
# generation) is intentionally excluded from the comparison.
only_restartedAt_change if {
	input.request.namespace != "chutes"

	req_object.spec.selector == oldObject.spec.selector

	object.get(req_object.spec.template, "spec", {}) == object.get(oldObject.spec.template, "spec", {})

	object.get(req_object.spec.template.metadata, "labels", {}) == object.get(oldObject.spec.template.metadata, "labels", {})

	new_annotations := object.get(req_object.spec.template.metadata, "annotations", {})
	old_annotations := object.get(oldObject.spec.template.metadata, "annotations", {})
	_without_restarted_at(new_annotations) == _without_restarted_at(old_annotations)

	new_restarted_at := new_annotations["kubectl.kubernetes.io/restartedAt"]
	new_restarted_at != object.get(old_annotations, "kubectl.kubernetes.io/restartedAt", "")
}

_without_restarted_at(ann) := {k: v | some k; v := ann[k]; k != "kubectl.kubernetes.io/restartedAt"}
