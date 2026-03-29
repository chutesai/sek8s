# OPA tests for seccomp profile enforcement in chutes namespace.
# SEK8S-042: No seccompProfile may be specified — containerd default is
# user-workload.json set at VM build time. Any override weakens it.
# Run locally: make test-opa-policies (or: opa test <policies-dir> tests/opa -v)
package kubernetes.admission

import future.keywords.if
import future.keywords.in

# =============================================================================
# Pod-level seccomp: any specification must be denied
# =============================================================================

test_deny_pod_with_unconfined_seccomp if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {
					"runAsUser": 1000,
					"seccompProfile": {"type": "Unconfined"},
				},
				"automountServiceAccountToken": false,
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "seccompProfile")}) > 0 with input as {"request": req}
}

test_deny_pod_with_runtime_default_seccomp if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {
					"runAsUser": 1000,
					"seccompProfile": {"type": "RuntimeDefault"},
				},
				"automountServiceAccountToken": false,
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "seccompProfile")}) > 0 with input as {"request": req}
}

test_deny_pod_with_localhost_seccomp if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {
					"runAsUser": 1000,
					"seccompProfile": {"type": "Localhost", "localhostProfile": "user-workload.json"},
				},
				"automountServiceAccountToken": false,
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "seccompProfile")}) > 0 with input as {"request": req}
}

# =============================================================================
# Container-level seccomp: any specification must be denied
# =============================================================================

test_deny_container_with_seccomp_override if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"automountServiceAccountToken": false,
				"containers": [{
					"name": "app",
					"image": "busybox",
					"resources": {"limits": {"memory": "1Gi"}},
					"securityContext": {"seccompProfile": {"type": "Unconfined"}},
				}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "seccompProfile")}) > 0 with input as {"request": req}
}

# =============================================================================
# Job template: any specification must be denied
# =============================================================================

test_deny_job_with_seccomp_override if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Job"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {"template": {"metadata": {"labels": {"chutes/chute": "true"}}, "spec": {
				"securityContext": {
					"runAsUser": 1000,
					"seccompProfile": {"type": "RuntimeDefault"},
				},
				"automountServiceAccountToken": false,
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
			}}},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "seccompProfile")}) > 0 with input as {"request": req}
}

# =============================================================================
# Allowed: only when seccompProfile is completely omitted
# =============================================================================

test_allow_pod_without_seccomp_specified if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"automountServiceAccountToken": false,
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "seccompProfile")}) == 0 with input as {"request": req}
}

# =============================================================================
# System namespace: no enforcement
# =============================================================================

test_allow_seccomp_in_system_namespace if {
	req := {
		"operation": "CREATE",
		"namespace": "kube-system",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {
					"runAsUser": 0,
					"seccompProfile": {"type": "Unconfined"},
				},
				"containers": [{"name": "coredns", "image": "rancher/coredns:latest"}],
			},
		},
		"userInfo": {"username": "system:serviceaccount:kube-system:coredns"},
	}
	count({m | deny[m]; contains(m, "seccompProfile")}) == 0 with input as {"request": req}
}
