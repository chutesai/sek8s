# Restrict PATCH on Deployment/DaemonSet outside chutes to restartedAt-only.
# In namespaces other than chutes, the only allowed patch is
# spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"] (rollout restart).
# chutes namespace allows full patches. Policy is baked in at build time.
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

# Only restartedAt annotation may change; pod spec and other metadata must be unchanged
only_restartedAt_change if {
	object.spec.template.spec == oldObject.spec.template.spec
	object.spec.template.metadata.labels == oldObject.spec.template.metadata.labels
	annotation_only_restartedAt_change(new_annotations, old_annotations_raw)
	deployment_daemonset_spec_unchanged
}

object := input.request.object
oldObject := input.request.oldObject
new_annotations := object.spec.template.metadata.annotations
old_annotations_raw := oldObject.spec.template.metadata.annotations

# Annotations may differ only by kubectl.kubernetes.io/restartedAt (add/update)
annotation_only_restartedAt_change(new_ann, old_ann) if {
	restartedAt_key := "kubectl.kubernetes.io/restartedAt"
	count({k | new_ann[k]; k != restartedAt_key; new_ann[k] != old_ann[k]}) == 0
	count({k | old_ann[k]; k != restartedAt_key; new_ann[k] != old_ann[k]}) == 0
}

# Top-level spec fields (other than template) must be unchanged
deployment_daemonset_spec_unchanged if {
	object.spec.selector == oldObject.spec.selector
	input.request.kind.kind == "Deployment"
	object.spec.replicas == oldObject.spec.replicas
	object.spec.strategy == oldObject.spec.strategy
}

deployment_daemonset_spec_unchanged if {
	object.spec.selector == oldObject.spec.selector
	input.request.kind.kind == "DaemonSet"
	object.spec.updateStrategy == oldObject.spec.updateStrategy
}
