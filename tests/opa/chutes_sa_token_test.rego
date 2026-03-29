# OPA tests for automountServiceAccountToken enforcement in chutes namespace.
# SEK8S-039: defense-in-depth validation after mutating webhook sets the field.
# Run locally: make test-opa-policies (or: opa test <policies-dir> tests/opa -v)
package kubernetes.admission

import future.keywords.if
import future.keywords.in

# =============================================================================
# Pod: automountServiceAccountToken must be false in chutes namespace
# =============================================================================

test_deny_pod_without_automount_false if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) > 0 with input as {"request": req}
}

test_deny_pod_with_automount_true if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"automountServiceAccountToken": true,
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) > 0 with input as {"request": req}
}

test_allow_pod_with_automount_false if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"automountServiceAccountToken": false,
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) == 0 with input as {"request": req}
}

# =============================================================================
# Job: automountServiceAccountToken must be false in chutes namespace
# =============================================================================

test_deny_job_without_automount_false if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Job"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {"template": {"metadata": {"labels": {"chutes/chute": "true"}}, "spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
			}}},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) > 0 with input as {"request": req}
}

test_allow_job_with_automount_false if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Job"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {"template": {"metadata": {"labels": {"chutes/chute": "true"}}, "spec": {
				"automountServiceAccountToken": false,
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
			}}},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) == 0 with input as {"request": req}
}

# =============================================================================
# Agent exemption: Deployment with agent label + image is allowed automount
# =============================================================================

test_allow_agent_deployment_with_automount_true if {
	req := {
		"operation": "UPDATE",
		"namespace": "chutes",
		"kind": {"kind": "Deployment"},
		"object": {
			"metadata": {"name": "agent"},
			"spec": {"template": {"metadata": {"labels": {"app.kubernetes.io/name": "agent"}}, "spec": {
				"automountServiceAccountToken": true,
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "agent", "image": "parachutes/chutes-agent:k3s-latest"}],
			}}},
		},
		"userInfo": {"username": "system:admin", "groups": ["system:masters"]},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) == 0 with input as {"request": req}
}

test_allow_agent_pod_created_by_controller if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"app.kubernetes.io/name": "agent"}},
			"spec": {
				"automountServiceAccountToken": true,
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "agent", "image": "parachutes/chutes-agent:k3s-latest"}],
			},
		},
		"userInfo": {"username": "system:serviceaccount:kube-system:replicaset-controller"},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) == 0 with input as {"request": req}
}

# =============================================================================
# Agent exemption: miner cannot abuse the exemption
# =============================================================================

test_deny_miner_pod_mimicking_agent_label if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"app.kubernetes.io/name": "agent"}},
			"spec": {
				"automountServiceAccountToken": true,
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "agent", "image": "parachutes/chutes-agent:k3s-latest"}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) > 0 with input as {"request": req}
}

test_deny_miner_pod_agent_label_wrong_image if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"app.kubernetes.io/name": "agent"}},
			"spec": {
				"automountServiceAccountToken": true,
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "agent", "image": "evil/agent:latest"}],
			},
		},
		"userInfo": {"username": "system:serviceaccount:kube-system:replicaset-controller"},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) > 0 with input as {"request": req}
}

test_deny_controller_pod_wrong_label_agent_image if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"app.kubernetes.io/name": "not-agent"}},
			"spec": {
				"automountServiceAccountToken": true,
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "agent", "image": "parachutes/chutes-agent:k3s-latest"}],
			},
		},
		"userInfo": {"username": "system:serviceaccount:kube-system:replicaset-controller"},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) > 0 with input as {"request": req}
}

test_deny_job_with_agent_label_automount_true if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Job"},
		"object": {
			"metadata": {"labels": {"app.kubernetes.io/name": "agent"}},
			"spec": {"template": {"metadata": {"labels": {"app.kubernetes.io/name": "agent"}}, "spec": {
				"automountServiceAccountToken": true,
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "agent", "image": "parachutes/chutes-agent:k3s-latest"}],
			}}},
		},
		"userInfo": {"username": "system:serviceaccount:kube-system:job-controller"},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) > 0 with input as {"request": req}
}

# =============================================================================
# System namespace: no enforcement (system controllers need SA tokens)
# =============================================================================

test_allow_pod_in_system_namespace_without_automount_false if {
	req := {
		"operation": "CREATE",
		"namespace": "kube-system",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 0},
				"containers": [{"name": "coredns", "image": "rancher/coredns:latest"}],
			},
		},
		"userInfo": {"username": "system:serviceaccount:kube-system:coredns"},
	}
	count({m | deny[m]; contains(m, "automountServiceAccountToken")}) == 0 with input as {"request": req}
}
