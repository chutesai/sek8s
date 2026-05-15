package kubernetes.admission

import future.keywords.contains
import future.keywords.if
import future.keywords.in

import data.helpers

# =============================================================================
# CHUTES NAMESPACE: NO ROOT / NO SUDO
# =============================================================================
# Pod-spec rules (root, runAsUser, runAsNonRoot, command) apply on CREATE for all
# resources, and on UPDATE for higher-level resources whose template spec is mutable.
# Pod specs are immutable after creation so UPDATE validation is redundant and would
# block operations (e.g. finalizer removal) on pre-existing pods.
chutes_apply_pod_spec_rules if {
	input.request.operation == "CREATE"
}

chutes_apply_pod_spec_rules if {
	input.request.operation == "UPDATE"
	input.request.kind.kind != "Pod"
}

# In chutes namespace no pod/container may run as root (UID 0). There is exactly
# one exception: the init container named "cache-init" with image parachutes/cache-cleaner*
# may run as root to chmod the hostPath cache dir that kubelet may create as root.
# To allow root anywhere else, the only place to change is chutes_is_cache_cleaner_init
# and the logic in chutes_container_runs_root_denied below.

# ONLY root exception: cache-init with parachutes/cache-cleaner (name + image).
# Image from Docker Hub is e.g. parachutes/cache-cleaner:release-next-latest (no docker.io prefix in spec).
chutes_is_cache_cleaner_init(container) if {
	container.name == "cache-init"
	regex.match("^parachutes/cache-cleaner[:@]", container.image)
}

# Effective runAsUser for a container: container override or pod-level default
chutes_effective_run_as_user(container, pod_spec) := uid if {
	uid := container.securityContext.runAsUser
}
chutes_effective_run_as_user(container, pod_spec) := uid if {
	not container.securityContext.runAsUser
	uid := pod_spec.securityContext.runAsUser
}

# True when this container runs as root and that is not allowed (deny).
# Single place for root-denial logic: main/ephemeral always denied if root;
# init denied if root unless chutes_is_cache_cleaner_init(container).
chutes_container_runs_root_denied(container, pod_spec, is_init_container) if {
	chutes_effective_run_as_user(container, pod_spec) == 0
	not is_init_container
}
chutes_container_runs_root_denied(container, pod_spec, is_init_container) if {
	chutes_effective_run_as_user(container, pod_spec) == 0
	is_init_container
	not chutes_is_cache_cleaner_init(container)
}

# Deny chutes namespace if pod-level runAsUser is root
deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	input.request.object.spec.securityContext.runAsUser == 0
	msg := "Chutes namespace: pods must not run as root (runAsUser: 0)"
}

# Deny when pod-level runAsUser is unspecified (would allow image default, often root)
deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	not input.request.object.spec.securityContext.runAsUser
	msg := "Chutes namespace: pod spec must set securityContext.runAsUser to a non-zero value"
}

# Deny chutes namespace if any container runs as root (uses single helper for exception)
deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.containers[_]
	chutes_container_runs_root_denied(container, input.request.object.spec, false)
	msg := sprintf("Chutes namespace: container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.initContainers[_]
	chutes_container_runs_root_denied(container, input.request.object.spec, true)
	msg := sprintf("Chutes namespace: init container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.ephemeralContainers[_]
	chutes_container_runs_root_denied(container, input.request.object.spec, false)
	msg := sprintf("Chutes namespace: ephemeral container '%s' must not run as root (runAsUser: 0)", [container.name])
}


# Same for workload templates (Deployment, StatefulSet, DaemonSet, ReplicaSet, Job, CronJob)
deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	input.request.object.spec.template.spec.securityContext.runAsUser == 0
	msg := "Chutes namespace: pods must not run as root (runAsUser: 0)"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	not input.request.object.spec.template.spec.securityContext.runAsUser
	msg := "Chutes namespace: pod spec must set securityContext.runAsUser to a non-zero value"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	chutes_container_runs_root_denied(container, input.request.object.spec.template.spec, false)
	msg := sprintf("Chutes namespace: container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.initContainers[_]
	chutes_container_runs_root_denied(container, input.request.object.spec.template.spec, true)
	msg := sprintf("Chutes namespace: init container '%s' must not run as root (runAsUser: 0)", [container.name])
}


deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	input.request.object.spec.template.spec.securityContext.runAsUser == 0
	msg := "Chutes namespace: pods must not run as root (runAsUser: 0)"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	not input.request.object.spec.template.spec.securityContext.runAsUser
	msg := "Chutes namespace: pod spec must set securityContext.runAsUser to a non-zero value"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	chutes_container_runs_root_denied(container, input.request.object.spec.template.spec, false)
	msg := sprintf("Chutes namespace: container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.initContainers[_]
	chutes_container_runs_root_denied(container, input.request.object.spec.template.spec, true)
	msg := sprintf("Chutes namespace: init container '%s' must not run as root (runAsUser: 0)", [container.name])
}


deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	input.request.object.spec.jobTemplate.spec.template.spec.securityContext.runAsUser == 0
	msg := "Chutes namespace: pods must not run as root (runAsUser: 0)"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	not input.request.object.spec.jobTemplate.spec.template.spec.securityContext.runAsUser
	msg := "Chutes namespace: pod spec must set securityContext.runAsUser to a non-zero value"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	container := input.request.object.spec.jobTemplate.spec.template.spec.containers[_]
	chutes_container_runs_root_denied(container, input.request.object.spec.jobTemplate.spec.template.spec, false)
	msg := sprintf("Chutes namespace: container '%s' must not run as root (runAsUser: 0)", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	container := input.request.object.spec.jobTemplate.spec.template.spec.initContainers[_]
	chutes_container_runs_root_denied(container, input.request.object.spec.jobTemplate.spec.template.spec, true)
	msg := sprintf("Chutes namespace: init container '%s' must not run as root (runAsUser: 0)", [container.name])
}


# =============================================================================
# CHUTES NAMESPACE: runAsNonRoot FOR NON-CHUTE WORKLOADS
# =============================================================================
# All pods in chutes must set runAsNonRoot: true except the chute workload: a Job
# (and the Pod that Job creates) from build_chute_job, which uses cache-init with runAsUser: 0.
# Only Job and Pod with label chutes/chute: "true" are treated as chute workloads;
# Deployment/StatefulSet/DaemonSet/ReplicaSet/CronJob with that label are not legitimate.

chutes_is_chute_workload if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	input.request.object.metadata.labels["chutes/chute"] == "true"
}
chutes_is_chute_workload if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	input.request.object.spec.template.metadata.labels["chutes/chute"] == "true"
}

# Deny non-chute pods in chutes that do not set runAsNonRoot: true
deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	not chutes_is_chute_workload
	not input.request.object.spec.securityContext.runAsNonRoot
	msg := "Chutes namespace: pod spec must set securityContext.runAsNonRoot: true (chute workloads excepted)"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	not input.request.object.spec.template.spec.securityContext.runAsNonRoot
	msg := "Chutes namespace: pod spec must set securityContext.runAsNonRoot: true (chute workloads excepted)"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	not chutes_is_chute_workload
	not input.request.object.spec.template.spec.securityContext.runAsNonRoot
	msg := "Chutes namespace: pod spec must set securityContext.runAsNonRoot: true (chute workloads excepted)"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	not input.request.object.spec.jobTemplate.spec.template.spec.securityContext.runAsNonRoot
	msg := "Chutes namespace: pod spec must set securityContext.runAsNonRoot: true (chute workloads excepted)"
}


# =============================================================================
# CHUTES NAMESPACE: NO SERVICE ACCOUNT TOKEN
# =============================================================================
# Defense-in-depth: the mutating webhook sets automountServiceAccountToken: false
# before validation runs. These rules catch anything the mutator misses.
#
# Exception: the agent Deployment needs in-cluster API access. At the Pod level
# we verify the request originates from a system controller (replicaset-controller),
# not the miner, to prevent a miner from creating a bare pod that mimics the agent.

chutes_agent_sa_token_exempt if {
	input.request.kind.kind == "Pod"
	helpers.is_system_or_controller_user
	input.request.object.metadata.labels["app.kubernetes.io/name"] == "agent"
	has_agent_image(input.request.object.spec)
}

chutes_agent_sa_token_exempt if {
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_system_or_controller_user
	input.request.object.spec.template.metadata.labels["app.kubernetes.io/name"] == "agent"
	has_agent_image(input.request.object.spec.template.spec)
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	not chutes_agent_sa_token_exempt
	not input.request.object.spec.automountServiceAccountToken == false
	msg := "Chutes namespace: pod spec must set automountServiceAccountToken: false"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	not chutes_agent_sa_token_exempt
	not input.request.object.spec.template.spec.automountServiceAccountToken == false
	msg := "Chutes namespace: pod spec must set automountServiceAccountToken: false"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	not input.request.object.spec.template.spec.automountServiceAccountToken == false
	msg := "Chutes namespace: pod spec must set automountServiceAccountToken: false"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	not input.request.object.spec.jobTemplate.spec.template.spec.automountServiceAccountToken == false
	msg := "Chutes namespace: pod spec must set automountServiceAccountToken: false"
}

# =============================================================================
# CHUTES NAMESPACE: NO envFrom
# =============================================================================
# envFrom bulk-injects all keys from a ConfigMap/Secret as env vars, bypassing
# the per-name allowlist in pods.rego. All env vars must use explicit env[]
# entries so each name is validated.

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.containers[_]
	container.envFrom
	msg := sprintf("Chutes namespace: container '%s' must not use envFrom (use explicit env[] entries)", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.initContainers[_]
	container.envFrom
	msg := sprintf("Chutes namespace: init container '%s' must not use envFrom", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.ephemeralContainers[_]
	container.envFrom
	msg := sprintf("Chutes namespace: ephemeral container '%s' must not use envFrom", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	container.envFrom
	msg := sprintf("Chutes namespace: container '%s' must not use envFrom (use explicit env[] entries)", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.initContainers[_]
	container.envFrom
	msg := sprintf("Chutes namespace: init container '%s' must not use envFrom", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	container.envFrom
	msg := sprintf("Chutes namespace: container '%s' must not use envFrom (use explicit env[] entries)", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.initContainers[_]
	container.envFrom
	msg := sprintf("Chutes namespace: init container '%s' must not use envFrom", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	container := input.request.object.spec.jobTemplate.spec.template.spec.containers[_]
	container.envFrom
	msg := sprintf("Chutes namespace: container '%s' must not use envFrom (use explicit env[] entries)", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	container := input.request.object.spec.jobTemplate.spec.template.spec.initContainers[_]
	container.envFrom
	msg := sprintf("Chutes namespace: init container '%s' must not use envFrom", [container.name])
}

# =============================================================================
# CHUTES NAMESPACE: SECCOMP PROFILE ENFORCEMENT
# =============================================================================
# Containerd defaults to user-workload.json (set at VM build time). Any
# explicit seccompProfile override — even RuntimeDefault or Localhost —
# would weaken or replace it. Deny all seccompProfile specifications.

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	input.request.object.spec.securityContext.seccompProfile
	msg := "Chutes namespace: seccompProfile must not be specified (containerd default is enforced at build time)"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.containers[_]
	container.securityContext.seccompProfile
	msg := sprintf("Chutes namespace: container '%s' must not specify seccompProfile", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.initContainers[_]
	container.securityContext.seccompProfile
	msg := sprintf("Chutes namespace: init container '%s' must not specify seccompProfile", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	input.request.object.spec.template.spec.securityContext.seccompProfile
	msg := "Chutes namespace: seccompProfile must not be specified (containerd default is enforced at build time)"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	container.securityContext.seccompProfile
	msg := sprintf("Chutes namespace: container '%s' must not specify seccompProfile", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	input.request.object.spec.template.spec.securityContext.seccompProfile
	msg := "Chutes namespace: seccompProfile must not be specified (containerd default is enforced at build time)"
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	container.securityContext.seccompProfile
	msg := sprintf("Chutes namespace: container '%s' must not specify seccompProfile", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	input.request.object.spec.jobTemplate.spec.template.spec.securityContext.seccompProfile
	msg := "Chutes namespace: seccompProfile must not be specified (containerd default is enforced at build time)"
}

# =============================================================================
# CHUTES NAMESPACE: VOLUME TYPE ALLOWLIST
# =============================================================================
# Prevent code injection via filesystem overlays (e.g. mounting a ConfigMap over
# a Python package directory to replace cosign-verified image code).
#
# Allowed types:
#   - hostPath  (validated separately by volumes.rego)
#   - emptyDir  (starts empty, safe)
#   - projected (auto-injected by k8s for SA token, kube-root-ca.crt, downwardAPI)
#
# Denied: configMap, secret, persistentVolumeClaim, nfs, csi, etc.
#
# Pod-level checks exempt system/controller users (e.g. kube-system:daemon-set-controller
# creating registry pods). We use userInfo.username — set by the API server after
# authentication — NOT ownerReferences, which are user-settable metadata and could be
# forged by a miner to bypass this policy.

chutes_is_allowed_volume_type(volume) if { volume.hostPath }
chutes_is_allowed_volume_type(volume) if { volume.emptyDir != null }
chutes_is_allowed_volume_type(volume) if { volume.projected }

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	not helpers.is_system_or_controller_user
	volume := input.request.object.spec.volumes[_]
	not chutes_is_allowed_volume_type(volume)
	msg := sprintf("Chutes namespace: volume '%s' uses a forbidden type (only hostPath, emptyDir, and projected allowed)", [volume.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	volume := input.request.object.spec.template.spec.volumes[_]
	not chutes_is_allowed_volume_type(volume)
	msg := sprintf("Chutes namespace: volume '%s' uses a forbidden type (only hostPath, emptyDir, and projected allowed)", [volume.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	volume := input.request.object.spec.template.spec.volumes[_]
	not chutes_is_allowed_volume_type(volume)
	msg := sprintf("Chutes namespace: volume '%s' uses a forbidden type (only hostPath, emptyDir, and projected allowed)", [volume.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	volume := input.request.object.spec.jobTemplate.spec.template.spec.volumes[_]
	not chutes_is_allowed_volume_type(volume)
	msg := sprintf("Chutes namespace: volume '%s' uses a forbidden type (only hostPath, emptyDir, and projected allowed)", [volume.name])
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
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.containers[_]
	chutes_deny_container(container)
	msg := chutes_deny_message(container)
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.initContainers[_]
	chutes_deny_container(container)
	msg := sprintf("Chutes namespace: init container '%s' must not override command (use image entrypoint)", [container.name])
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Pod"
	helpers.is_pod_resource
	container := input.request.object.spec.ephemeralContainers[_]
	chutes_deny_container(container)
	msg := sprintf("Chutes namespace: ephemeral container '%s' must not override command (use image entrypoint)", [container.name])
}

# Deny Deployment/StatefulSet/DaemonSet/ReplicaSet in chutes namespace
deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	chutes_deny_container(container)
	msg := chutes_deny_message(container)
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"]
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.initContainers[_]
	chutes_deny_container(container)
	msg := sprintf("Chutes namespace: init container '%s' must not override command (use image entrypoint)", [container.name])
}

# Deny Job in chutes namespace
deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.containers[_]
	chutes_deny_container(container)
	msg := chutes_deny_message(container)
}

deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "Job"
	helpers.is_pod_resource
	container := input.request.object.spec.template.spec.initContainers[_]
	chutes_deny_container(container)
	msg := sprintf("Chutes namespace: init container '%s' must not override command (use image entrypoint)", [container.name])
}

# Deny CronJob in chutes namespace
deny contains msg if {
	chutes_apply_pod_spec_rules
	input.request.namespace == "chutes"
	input.request.kind.kind == "CronJob"
	helpers.is_pod_resource
	container := input.request.object.spec.jobTemplate.spec.template.spec.containers[_]
	chutes_deny_container(container)
	msg := chutes_deny_message(container)
}

deny contains msg if {
	chutes_apply_pod_spec_rules
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

# =============================================================================
# CHUTES NAMESPACE: CONFIGMAP MUTATIONS RESTRICTED TO SYSTEM CONTROLLERS
# =============================================================================
# The miner has RBAC for ConfigMaps (backwards compat with non-TEE deploys),
# but OPA denies all mutations from non-system users. This prevents:
#   - Modifying kube-root-ca.crt (CA injection for MITM)
#   - Modifying registry-nginx-config (registry traffic manipulation)
#   - Creating ConfigMaps for code injection (volume mount separately blocked)
#
# Only system/controller users (identified by userInfo.username, set by the
# API server after authentication) may create, update, or delete ConfigMaps.

deny contains msg if {
	input.request.namespace == "chutes"
	input.request.kind.kind == "ConfigMap"
	input.request.operation in ["CREATE", "UPDATE", "DELETE"]
	not helpers.is_system_or_controller_user
	msg := sprintf("Chutes namespace: ConfigMap operations restricted to system controllers (user '%s' denied)", [input.request.userInfo.username])
}
