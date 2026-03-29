# OPA tests for envFrom restriction in chutes namespace.
# SEK8S-044: envFrom bypasses the env var allowlist by bulk-injecting all
# keys from a ConfigMap/Secret. Block it entirely — all env vars must use
# explicit env[] entries which are validated against the allowlist.
# Run locally: make test-opa-policies (or: opa test <policies-dir> tests/opa -v)
package kubernetes.admission

import future.keywords.if
import future.keywords.in

# =============================================================================
# Pod: envFrom must be denied in chutes namespace
# =============================================================================

test_deny_pod_with_envfrom_configmap if {
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
					"envFrom": [{"configMapRef": {"name": "my-config"}}],
				}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "envFrom")}) > 0 with input as {"request": req}
}

test_deny_pod_with_envfrom_secret if {
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
					"envFrom": [{"secretRef": {"name": "my-secret"}}],
				}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "envFrom")}) > 0 with input as {"request": req}
}

# =============================================================================
# Job: envFrom must be denied in chutes namespace
# =============================================================================

test_deny_job_with_envfrom if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Job"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {"template": {"metadata": {"labels": {"chutes/chute": "true"}}, "spec": {
				"securityContext": {"runAsUser": 1000},
				"automountServiceAccountToken": false,
				"containers": [{
					"name": "app",
					"image": "busybox",
					"resources": {"limits": {"memory": "1Gi"}},
					"envFrom": [{"configMapRef": {"name": "code-config"}}],
				}],
			}}},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "envFrom")}) > 0 with input as {"request": req}
}

# =============================================================================
# Init container: envFrom must be denied too
# =============================================================================

test_deny_init_container_with_envfrom if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"automountServiceAccountToken": false,
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
				"initContainers": [{
					"name": "init",
					"image": "busybox",
					"envFrom": [{"configMapRef": {"name": "init-config"}}],
				}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "envFrom")}) > 0 with input as {"request": req}
}

# =============================================================================
# Allowed: valueFrom on individual env entries is fine
# =============================================================================

test_allow_pod_with_valuefrom_secret if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"automountServiceAccountToken": false,
				"containers": [{
					"name": "app",
					"image": "busybox",
					"resources": {"limits": {"memory": "1Gi"}},
					"env": [{"name": "MINER_SS58", "valueFrom": {"secretKeyRef": {"name": "miner-credentials", "key": "ss58"}}}],
				}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "envFrom")}) == 0 with input as {"request": req}
}

# =============================================================================
# Allowed: no envFrom, just plain env
# =============================================================================

test_allow_pod_with_plain_env if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"automountServiceAccountToken": false,
				"containers": [{
					"name": "app",
					"image": "busybox",
					"resources": {"limits": {"memory": "1Gi"}},
					"env": [{"name": "CUDA_VISIBLE_DEVICES", "value": "0"}],
				}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "envFrom")}) == 0 with input as {"request": req}
}

# =============================================================================
# System namespace: no enforcement
# =============================================================================

test_allow_envfrom_in_system_namespace if {
	req := {
		"operation": "CREATE",
		"namespace": "kube-system",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 0},
				"containers": [{
					"name": "coredns",
					"image": "rancher/coredns:latest",
					"envFrom": [{"configMapRef": {"name": "coredns-config"}}],
				}],
			},
		},
		"userInfo": {"username": "system:serviceaccount:kube-system:coredns"},
	}
	count({m | deny[m]; contains(m, "envFrom")}) == 0 with input as {"request": req}
}
