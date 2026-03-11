# Outside chutes namespace: only allow rollout restart (restartedAt-only patch).
# Chutes namespace: miner RBAC allows full patches; no restriction here.
# System/controller users exempted globally via effective_deny.
package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

# Deny UPDATE on Deployment/DaemonSet in non-chutes namespaces when patch changes more than restartedAt
deny contains msg if {
	input.request.namespace != "chutes"
	input.request.operation == "UPDATE"
	input.request.kind.kind in ["Deployment", "DaemonSet"]
	not only_restartedAt_change
	msg := "Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"
}

req_object := input.request.object
oldObject := input.request.oldObject
# Use object.get to handle missing annotations (rollout restart adds restartedAt to previously empty metadata)
new_annotations := object.get(req_object.spec.template.metadata, "annotations", {})
old_annotations_raw := object.get(oldObject.spec.template.metadata, "annotations", {})

# RestartedAt-only: annotations differ only by restartedAt, selector unchanged, restartedAt present
only_restartedAt_change if {
	input.request.namespace != "chutes"
	annotation_only_restartedAt_change(new_annotations, old_annotations_raw)
	req_object.spec.selector == oldObject.spec.selector
	new_annotations["kubectl.kubernetes.io/restartedAt"]
}

# Annotations may differ only by kubectl.kubernetes.io/restartedAt (add/update)
# Caller uses object.get(..., "annotations", {}) so new_ann/old_ann are always objects
annotation_only_restartedAt_change(new_ann, old_ann) if {
	restartedAt_key := "kubectl.kubernetes.io/restartedAt"
	count({k | new_ann[k]; k != restartedAt_key; new_ann[k] != old_ann[k]}) == 0
	count({k | old_ann[k]; k != restartedAt_key; new_ann[k] != old_ann[k]}) == 0
}
