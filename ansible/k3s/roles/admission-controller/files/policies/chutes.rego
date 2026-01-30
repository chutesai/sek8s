package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

import data.helpers

# =============================================================================
# CHUTES NAMESPACE: COMMAND RESTRICTIONS
# =============================================================================
# In chutes namespace:
# - All containers (including init) must use image entrypoint only: no command override.
# - Exception: the main container named "chute" may set command but it must start
#   with ["chutes", "run"] (dynamic args after that are allowed).

# True when this container in chutes namespace should be denied (command override or invalid chute command)
chutes_deny_container(container) if {
	container.command
	container.name != "chute"
}

chutes_deny_container(container) if {
	container.command
	container.name == "chute"
	count(container.command) < 2
}

chutes_deny_container(container) if {
	container.command
	container.name == "chute"
	container.command[0] != "chutes"
}

chutes_deny_container(container) if {
	container.command
	container.name == "chute"
	container.command[1] != "run"
}

# Deny Pod in chutes namespace
deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.containers[_]
	chutes_deny_container(container)
	msg := chutes_deny_message(container)
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.initContainers[_]
	chutes_deny_container(container)
	msg := sprintf("Chutes namespace: init container '%s' must not override command (use image entrypoint)", [container.name])
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.ephemeralContainers[_]
	chutes_deny_container(container)
	msg := sprintf("Chutes namespace: ephemeral container '%s' must not override command (use image entrypoint)", [container.name])
}

# Deny Deployment/StatefulSet/DaemonSet/ReplicaSet in chutes namespace
deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	chutes_deny_container(container)
	msg := chutes_deny_message(container)
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.initContainers[_]
	chutes_deny_container(container)
	msg := sprintf("Chutes namespace: init container '%s' must not override command (use image entrypoint)", [container.name])
}

# Deny Job in chutes namespace
deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	chutes_deny_container(container)
	msg := chutes_deny_message(container)
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.initContainers[_]
	chutes_deny_container(container)
	msg := sprintf("Chutes namespace: init container '%s' must not override command (use image entrypoint)", [container.name])
}

# Deny CronJob in chutes namespace
deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	container := input.request.object.spec.jobTemplate.spec.template.spec.containers[_]
	chutes_deny_container(container)
	msg := chutes_deny_message(container)
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	container := input.request.object.spec.jobTemplate.spec.template.spec.initContainers[_]
	chutes_deny_container(container)
	msg := sprintf("Chutes namespace: init container '%s' must not override command (use image entrypoint)", [container.name])
}

# Message for main containers: chute must be "chutes run", others must not override
chutes_deny_message(container) := msg if {
	container.name == "chute"
	msg := sprintf("Chutes namespace: container '%s' command must start with ['chutes', 'run']", [container.name])
}

chutes_deny_message(container) := msg if {
	container.name != "chute"
	msg := sprintf("Chutes namespace: container '%s' must not override command (use image entrypoint)", [container.name])
}
