package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

import data.helpers

# =============================================================================
# CHUTES NAMESPACE: NO ROOT / NO SUDO
# =============================================================================
# In chutes namespace no pod may run as root (UID 0). Require runAsNonRoot: true
# and reject explicit runAsUser: 0.

# Effective runAsUser for a container: container override or pod-level default
chutes_effective_run_as_user(container, pod_spec) := uid if {
	uid := container.securityContext.runAsUser
}
chutes_effective_run_as_user(container, pod_spec) := uid if {
	not container.securityContext.runAsUser
	uid := pod_spec.securityContext.runAsUser
}

# Deny chutes namespace if pod-level runAsUser is root
deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	input.request.object.spec.securityContext.runAsUser == 0
	msg := "Chutes namespace: pods must not run as root (runAsUser: 0)"
}

# Deny chutes namespace if any container runs as root
deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.containers[_]
	chutes_effective_run_as_user(container, input.request.object.spec) == 0
	msg := sprintf("Chutes namespace: container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.initContainers[_]
	chutes_effective_run_as_user(container, input.request.object.spec) == 0
	msg := sprintf("Chutes namespace: init container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.ephemeralContainers[_]
	chutes_effective_run_as_user(container, input.request.object.spec) == 0
	msg := sprintf("Chutes namespace: ephemeral container '%s' must not run as root (runAsUser: 0)", [container.name])
}

# Require runAsNonRoot: true at pod level in chutes
deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	input.request.object.spec.securityContext.runAsNonRoot != true
	msg := "Chutes namespace: pod must set securityContext.runAsNonRoot: true"
}

# Same for workload templates (Deployment, StatefulSet, DaemonSet, ReplicaSet, Job, CronJob)
deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	input.request.object.spec.template.spec.securityContext.runAsUser == 0
	msg := "Chutes namespace: pods must not run as root (runAsUser: 0)"
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	chutes_effective_run_as_user(container, input.request.object.spec.template.spec) == 0
	msg := sprintf("Chutes namespace: container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.initContainers[_]
	chutes_effective_run_as_user(container, input.request.object.spec.template.spec) == 0
	msg := sprintf("Chutes namespace: init container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	input.request.object.spec.template.spec.securityContext.runAsNonRoot != true
	msg := "Chutes namespace: pod must set securityContext.runAsNonRoot: true"
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	input.request.object.spec.template.spec.securityContext.runAsUser == 0
	msg := "Chutes namespace: pods must not run as root (runAsUser: 0)"
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	chutes_effective_run_as_user(container, input.request.object.spec.template.spec) == 0
	msg := sprintf("Chutes namespace: container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.initContainers[_]
	chutes_effective_run_as_user(container, input.request.object.spec.template.spec) == 0
	msg := sprintf("Chutes namespace: init container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	input.request.object.spec.template.spec.securityContext.runAsNonRoot != true
	msg := "Chutes namespace: pod must set securityContext.runAsNonRoot: true"
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	input.request.object.spec.jobTemplate.spec.template.spec.securityContext.runAsUser == 0
	msg := "Chutes namespace: pods must not run as root (runAsUser: 0)"
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	container := input.request.object.spec.jobTemplate.spec.template.spec.containers[_]
	chutes_effective_run_as_user(container, input.request.object.spec.jobTemplate.spec.template.spec) == 0
	msg := sprintf("Chutes namespace: container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	container := input.request.object.spec.jobTemplate.spec.template.spec.initContainers[_]
	chutes_effective_run_as_user(container, input.request.object.spec.jobTemplate.spec.template.spec) == 0
	msg := sprintf("Chutes namespace: init container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	input.request.object.spec.jobTemplate.spec.template.spec.securityContext.runAsNonRoot != true
	msg := "Chutes namespace: pod must set securityContext.runAsNonRoot: true"
}

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
