# OPA tests for chutes namespace image registry allowlist and ephemeral container deny.
# Covers no ephemeral containers and registry allowlist.
# Run locally: make test-opa-policies (or: opa test <policies-dir> tests/opa -v)
package kubernetes.admission

import future.keywords.if
import future.keywords.in

# Shared registry prefix used by the test config.json
# data.config.validator_registry = "testvalidator.localregistry.chutes.ai:30500"

# =============================================================================
# IMAGE REGISTRY ALLOWLIST — chutes_is_allowed_image
# =============================================================================

test_allow_validator_registry_image if {
	container := {"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}
	chutes_is_allowed_image(container) with data.config.validator_registry as "testvalidator.localregistry.chutes.ai:30500"
}

test_allow_parachutes_image if {
	container := {"name": "agent", "image": "parachutes/chutes-agent:latest"}
	chutes_is_allowed_image(container) with data.config.validator_registry as "testvalidator.localregistry.chutes.ai:30500"
}

test_deny_arbitrary_image if {
	container := {"name": "evil", "image": "docker.io/attacker/malware:latest"}
	not chutes_is_allowed_image(container) with data.config.validator_registry as "testvalidator.localregistry.chutes.ai:30500"
}

# Registry prefix match must require "/" delimiter — "testvalidator...ai:30500-evil/..." must NOT match.
test_deny_registry_prefix_adjacent_no_slash if {
	container := {"name": "evil", "image": "testvalidator.localregistry.chutes.ai:30500-evil/model:v1"}
	not chutes_is_allowed_image(container) with data.config.validator_registry as "testvalidator.localregistry.chutes.ai:30500"
}

# "parachutes" prefix without trailing "/" must NOT match.
test_deny_parachutes_prefix_adjacent_no_slash if {
	container := {"name": "evil", "image": "parachutes-evil/malware:latest"}
	not chutes_is_allowed_image(container) with data.config.validator_registry as "testvalidator.localregistry.chutes.ai:30500"
}

# =============================================================================
# IMAGE REGISTRY ALLOWLIST — deny rules on Pod in chutes namespace
# =============================================================================

test_deny_pod_with_disallowed_image_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"containers": [{"name": "evil", "image": "docker.io/attacker/malware:latest"}],
			},
		},
	}
	count({m | deny[m]; contains(m, "not from an allowed registry")}) > 0 with input as {"request": req}
}

test_allow_pod_with_validator_registry_image_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
			},
		},
	}
	count({m | deny[m]; contains(m, "not from an allowed registry")}) == 0 with input as {"request": req}
}

test_allow_pod_with_parachutes_image_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"labels": {}},
			"spec": {
				"containers": [{"name": "agent", "image": "parachutes/chutes-agent:latest"}],
			},
		},
	}
	count({m | deny[m]; contains(m, "not from an allowed registry")}) == 0 with input as {"request": req}
}

# System/controller users are exempt from the registry allowlist.
test_allow_system_user_any_image_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"userInfo": {"username": "system:serviceaccount:kube-system:helm-install-job"},
		"object": {
			"metadata": {"labels": {}},
			"spec": {
				"containers": [{"name": "job", "image": "some-internal-image:latest"}],
			},
		},
	}
	count({m | deny[m]; contains(m, "not from an allowed registry")}) == 0 with input as {"request": req}
}

# Registry prefix collision: evil registry sharing a prefix must be denied.
test_deny_pod_with_registry_prefix_collision_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"containers": [{"name": "evil", "image": "testvalidator.localregistry.chutes.ai:30500-evil/model:v1"}],
			},
		},
	}
	count({m | deny[m]; contains(m, "not from an allowed registry")}) > 0 with input as {"request": req}
}

# =============================================================================
# EPHEMERAL CONTAINERS — blanket deny in chutes namespace
# =============================================================================

test_deny_ephemeral_container_on_create_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
				"ephemeralContainers": [{"name": "debug", "image": "busybox"}],
			},
		},
	}
	deny["Chutes namespace: ephemeral containers are not permitted (no kubectl debug in production)"] with input as {"request": req}
}

test_deny_ephemeral_container_on_update_in_chutes if {
	req := {
		"operation": "UPDATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
				"ephemeralContainers": [{"name": "debug", "image": "busybox"}],
			},
		},
		"oldObject": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
			},
		},
	}
	deny["Chutes namespace: ephemeral containers are not permitted (no kubectl debug in production)"] with input as {"request": req}
}

# Pod without ephemeral containers must not trigger the deny.
test_allow_pod_without_ephemeral_containers_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
			},
		},
	}
	not deny["Chutes namespace: ephemeral containers are not permitted (no kubectl debug in production)"] with input as {"request": req}
}

# Ephemeral containers in a non-chutes namespace must not trigger this specific rule.
test_allow_ephemeral_container_outside_chutes_namespace if {
	req := {
		"operation": "UPDATE",
		"namespace": "default",
		"kind": {"kind": "Pod"},
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"labels": {}},
			"spec": {
				"containers": [{"name": "app", "image": "parachutes/some-app:latest"}],
				"ephemeralContainers": [{"name": "debug", "image": "busybox"}],
			},
		},
		"oldObject": {
			"metadata": {"labels": {}},
			"spec": {
				"containers": [{"name": "app", "image": "parachutes/some-app:latest"}],
			},
		},
	}
	not deny["Chutes namespace: ephemeral containers are not permitted (no kubectl debug in production)"] with input as {"request": req}
}

# =============================================================================
# SECURITY_OP — UPDATE enforcement in chutes namespace
# =============================================================================

# Adding privileged=true via UPDATE in chutes namespace must be denied.
test_deny_chutes_pod_update_adds_privileged if {
	req := {
		"operation": "UPDATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1", "securityContext": {"privileged": true}}],
			},
		},
		"oldObject": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
			},
		},
	}
	count({m | deny[m]; contains(m, "privileged")}) > 0 with input as {"request": req}
}

# =============================================================================
# IS_ROLLOUT_RESTART BYPASS ATTACK SCENARIOS
# =============================================================================
# An attacker cannot use a restartedAt annotation change to bypass security rules
# by simultaneously mutating the pod template spec. miner_restart.rego is the
# primary backstop, but is_rollout_restart itself requires spec.template.spec
# to be unchanged — so pods.rego and volumes.rego also independently block these.

# Changing restartedAt + adding privileged=true on a Deployment must be denied.
test_deny_rollout_restart_with_added_privileged if {
	req := {
		"operation": "UPDATE",
		"namespace": "gpu-operator",
		"kind": {"kind": "Deployment"},
		"userInfo": {"username": "miner"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"app": "gpu-driver"}},
				"template": {
					"metadata": {"labels": {"app": "gpu-driver"}, "annotations": {"kubectl.kubernetes.io/restartedAt": "2026-05-15T10:00:00Z"}},
					"spec": {
						"containers": [{"name": "driver", "image": "nvcr.io/nvidia/driver:535", "securityContext": {"privileged": true}}],
					},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"app": "gpu-driver"}},
				"template": {
					"metadata": {"labels": {"app": "gpu-driver"}},
					"spec": {
						# spec differs from new — privileged was not present before
						"containers": [{"name": "driver", "image": "nvcr.io/nvidia/driver:535"}],
					},
				},
			},
		},
	}
	# is_rollout_restart must be FALSE (spec changed) so pods.rego fires
	count({m | deny[m]; contains(m, "privileged")}) > 0 with input as {"request": req}
}

# Changing restartedAt + adding hostNetwork on a DaemonSet must be denied.
test_deny_rollout_restart_with_added_hostnetwork if {
	req := {
		"operation": "UPDATE",
		"namespace": "kube-system",
		"kind": {"kind": "DaemonSet"},
		"userInfo": {"username": "miner"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"app": "net-monitor"}},
				"template": {
					"metadata": {"labels": {"app": "net-monitor"}, "annotations": {"kubectl.kubernetes.io/restartedAt": "2026-05-15T10:00:00Z"}},
					"spec": {
						"hostNetwork": true,
						"containers": [{"name": "monitor", "image": "parachutes/net-monitor:latest"}],
					},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"app": "net-monitor"}},
				"template": {
					"metadata": {"labels": {"app": "net-monitor"}},
					"spec": {
						# spec differs — hostNetwork was not present before
						"containers": [{"name": "monitor", "image": "parachutes/net-monitor:latest"}],
					},
				},
			},
		},
	}
	count({m | deny[m]; contains(m, "host network")}) > 0 with input as {"request": req}
}

# Changing restartedAt + adding an arbitrary hostPath on a DaemonSet must be denied.
test_deny_rollout_restart_with_added_evil_hostpath if {
	req := {
		"operation": "UPDATE",
		"namespace": "kube-system",
		"kind": {"kind": "DaemonSet"},
		"userInfo": {"username": "miner"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"app": "logger"}},
				"template": {
					"metadata": {"labels": {"app": "logger"}, "annotations": {"kubectl.kubernetes.io/restartedAt": "2026-05-15T10:00:00Z"}},
					"spec": {
						"containers": [{"name": "logger", "image": "parachutes/logger:latest"}],
						"volumes": [{"name": "host-keys", "hostPath": {"path": "/etc/ssh"}}],
					},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"app": "logger"}},
				"template": {
					"metadata": {"labels": {"app": "logger"}},
					"spec": {
						# spec differs — hostPath volume was not present before
						"containers": [{"name": "logger", "image": "parachutes/logger:latest"}],
					},
				},
			},
		},
	}
	count({m | deny[m]; contains(m, "hostPath volume")}) > 0 with input as {"request": req}
}

# Pure restartedAt restart of a legitimately privileged DaemonSet must still be allowed.
# (spec.template.spec is identical between old and new — only the annotation changed)
test_allow_rollout_restart_of_existing_privileged_daemonset if {
	req := {
		"operation": "UPDATE",
		"namespace": "gpu-operator",
		"kind": {"kind": "DaemonSet"},
		"userInfo": {"username": "miner"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"app": "gpu-driver"}},
				"template": {
					"metadata": {
						"labels": {"app": "gpu-driver"},
						"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-05-15T10:00:00Z"},
					},
					"spec": {
						"hostNetwork": true,
						"containers": [{"name": "driver", "image": "nvcr.io/nvidia/driver:535", "securityContext": {"privileged": true, "capabilities": {"add": ["SYS_ADMIN"]}}}],
					},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"app": "gpu-driver"}},
				"template": {
					"metadata": {"labels": {"app": "gpu-driver"}},
					"spec": {
						# spec.template.spec is IDENTICAL to new — only annotation differs
						"hostNetwork": true,
						"containers": [{"name": "driver", "image": "nvcr.io/nvidia/driver:535", "securityContext": {"privileged": true, "capabilities": {"add": ["SYS_ADMIN"]}}}],
					},
				},
			},
		},
	}
	count({m | deny[m]; contains(m, "privileged")}) == 0 with input as {"request": req}
	count({m | deny[m]; contains(m, "host network")}) == 0 with input as {"request": req}
	count({m | deny[m]; contains(m, "dangerous capability")}) == 0 with input as {"request": req}
}
