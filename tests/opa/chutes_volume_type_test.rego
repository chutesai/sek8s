# OPA tests for volume type allowlist in chutes namespace.
# Prevents code injection via ConfigMap/Secret overlays on image filesystem.
# Validated against live pod manifests from local/chutes-pods.yaml.
# Run: make test-opa-policies
package kubernetes.admission

import future.keywords.if
import future.keywords.in

# =============================================================================
# DENY: ConfigMap volume on a chute Job (the primary attack vector)
# =============================================================================

test_chutes_deny_job_with_configmap_volume if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Job", "group": "batch"},
		"namespace": "chutes",
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {"labels": {"chutes/chute": "true"}},
					"spec": {
						"automountServiceAccountToken": false,
						"securityContext": {"runAsUser": 1000},
						"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1", "command": ["chutes", "run", "x:y"]}],
						"volumes": [
							{"name": "cache", "hostPath": {"path": "/var/snap/cache"}},
							{"name": "payload", "configMap": {"name": "evil-code"}},
						],
					},
				},
			},
		},
	}
	deny["Chutes namespace: volume 'payload' uses a forbidden type (only hostPath, emptyDir, and projected allowed)"] with input as {"request": req}
}

# =============================================================================
# DENY: Secret volume on a Pod (not DaemonSet-owned)
# =============================================================================

test_chutes_deny_pod_with_secret_volume if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Pod", "group": ""},
		"namespace": "chutes",
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"automountServiceAccountToken": false,
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1", "command": ["chutes", "run", "x:y"]}],
				"volumes": [{"name": "creds", "secret": {"secretName": "miner-credentials"}}],
			},
		},
	}
	deny["Chutes namespace: volume 'creds' uses a forbidden type (only hostPath, emptyDir, and projected allowed)"] with input as {"request": req}
}

# =============================================================================
# DENY: PersistentVolumeClaim
# =============================================================================

test_chutes_deny_pod_with_pvc_volume if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Pod", "group": ""},
		"namespace": "chutes",
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"automountServiceAccountToken": false,
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1", "command": ["chutes", "run", "x:y"]}],
				"volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "evil-pvc"}}],
			},
		},
	}
	deny["Chutes namespace: volume 'data' uses a forbidden type (only hostPath, emptyDir, and projected allowed)"] with input as {"request": req}
}

# =============================================================================
# DENY: ConfigMap volume on a Deployment
# =============================================================================

test_chutes_deny_deployment_with_configmap_volume if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Deployment", "group": "apps"},
		"namespace": "chutes",
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {"labels": {}},
					"spec": {
						"automountServiceAccountToken": false,
						"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
						"containers": [{"name": "agent", "image": "parachutes/chutes-agent:latest"}],
						"volumes": [{"name": "config", "configMap": {"name": "agent-config"}}],
					},
				},
			},
		},
	}
	deny["Chutes namespace: volume 'config' uses a forbidden type (only hostPath, emptyDir, and projected allowed)"] with input as {"request": req}
}

# =============================================================================
# DENY: ConfigMap volume on Pod created by Job controller (not DaemonSet)
# =============================================================================

test_chutes_deny_job_owned_pod_with_configmap_volume if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Pod", "group": ""},
		"namespace": "chutes",
		"object": {
			"metadata": {
				"labels": {"chutes/chute": "true"},
				"ownerReferences": [{"kind": "Job", "name": "chute-abc"}],
			},
			"spec": {
				"automountServiceAccountToken": false,
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1", "command": ["chutes", "run", "x:y"]}],
				"volumes": [{"name": "evil", "configMap": {"name": "evil-code"}}],
			},
		},
	}
	deny["Chutes namespace: volume 'evil' uses a forbidden type (only hostPath, emptyDir, and projected allowed)"] with input as {"request": req}
}

# =============================================================================
# ALLOW: Chute pod — hostPath + emptyDir + projected (matches live chute pod)
# =============================================================================

test_chutes_allow_chute_pod_live_volumes if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Pod", "group": ""},
		"namespace": "chutes",
		"object": {
			"metadata": {
				"labels": {"chutes/chute": "true"},
				"ownerReferences": [{"kind": "Job", "name": "chute-abc"}],
			},
			"spec": {
				"automountServiceAccountToken": false,
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1", "command": ["chutes", "run", "x:y"]}],
				"initContainers": [{"name": "cache-init", "image": "parachutes/cache-cleaner:latest", "securityContext": {"runAsUser": 0}}],
				"volumes": [
					{"name": "cache", "hostPath": {"path": "/var/snap/cache/abc"}},
					{"name": "raw-cache", "hostPath": {"path": "/var/snap/cache"}},
					{"name": "tmp", "emptyDir": {"sizeLimit": "10Gi"}},
					{"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "16Gi"}},
					{"name": "kube-api-access-qfc4v", "projected": {"sources": [
						{"serviceAccountToken": {"expirationSeconds": 3607, "path": "token"}},
						{"configMap": {"name": "kube-root-ca.crt", "items": [{"key": "ca.crt", "path": "ca.crt"}]}},
						{"downwardAPI": {"items": [{"fieldRef": {"fieldPath": "metadata.namespace"}, "path": "namespace"}]}},
					]}},
				],
			},
		},
	}
	count({msg | deny[msg]; startswith(msg, "Chutes namespace: volume")}) == 0 with input as {"request": req}
}

# =============================================================================
# ALLOW: Agent pod — hostPath + projected (matches live agent pod)
# =============================================================================

test_chutes_allow_agent_pod_live_volumes if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Pod", "group": ""},
		"namespace": "chutes",
		"object": {
			"metadata": {
				"labels": {"app.kubernetes.io/name": "agent"},
				"ownerReferences": [{"kind": "ReplicaSet", "name": "agent-6f886cb54d"}],
			},
			"spec": {
				"automountServiceAccountToken": false,
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "agent", "image": "parachutes/chutes-agent:k3s-latest"}],
				"volumes": [
					{"name": "chutes-agent-state", "hostPath": {"path": "/var/lib/chutes/agent", "type": "DirectoryOrCreate"}},
					{"name": "kube-api-access-q6kwb", "projected": {"sources": [
						{"serviceAccountToken": {"expirationSeconds": 3607, "path": "token"}},
						{"configMap": {"name": "kube-root-ca.crt", "items": [{"key": "ca.crt", "path": "ca.crt"}]}},
					]}},
				],
			},
		},
	}
	count({msg | deny[msg]; startswith(msg, "Chutes namespace: volume")}) == 0 with input as {"request": req}
}

# =============================================================================
# ALLOW: Registry pod — system controller user, configMap volume exempt
# =============================================================================
# The DaemonSet controller creates registry pods as a kube-system service account.
# userInfo.username is set by the API server and cannot be forged.

test_chutes_allow_registry_pod_system_controller if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Pod", "group": ""},
		"namespace": "chutes",
		"userInfo": {"username": "system:serviceaccount:kube-system:daemon-set-controller"},
		"object": {
			"metadata": {
				"labels": {"app.kubernetes.io/name": "chutes-registry"},
				"ownerReferences": [{"kind": "DaemonSet", "name": "registry"}],
			},
			"spec": {
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "registry", "image": "parachutes/nginx-proxy:latest"}],
				"volumes": [
					{"name": "registry-nginx-config", "configMap": {"name": "registry-nginx-config"}},
					{"name": "kube-api-access-jbvfl", "projected": {"sources": [
						{"serviceAccountToken": {"expirationSeconds": 3607, "path": "token"}},
					]}},
				],
			},
		},
	}
	count({msg | deny[msg]; startswith(msg, "Chutes namespace: volume")}) == 0 with input as {"request": req}
}

# =============================================================================
# DENY: Miner fakes DaemonSet ownerReference to bypass volume check
# =============================================================================
# Regression test: ownerReferences are user-settable metadata. A miner could
# set ownerReferences: [{kind: DaemonSet}] to try to bypass the volume allowlist.
# The policy must use userInfo (API-server-authenticated) not ownerReferences.

test_chutes_deny_fake_daemonset_owner_with_configmap if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Pod", "group": ""},
		"namespace": "chutes",
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {
				"labels": {"chutes/chute": "true"},
				"ownerReferences": [{"kind": "DaemonSet", "name": "registry"}],
			},
			"spec": {
				"automountServiceAccountToken": false,
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1", "command": ["chutes", "run", "x:y"]}],
				"volumes": [
					{"name": "evil-overlay", "configMap": {"name": "evil-code"}},
					{"name": "kube-api-access-abc", "projected": {"sources": []}},
				],
			},
		},
	}
	deny["Chutes namespace: volume 'evil-overlay' uses a forbidden type (only hostPath, emptyDir, and projected allowed)"] with input as {"request": req}
}

# =============================================================================
# ALLOW: Failed-chute-cleanup pod — projected only (matches live cleanup pod)
# =============================================================================

test_chutes_allow_cleanup_pod_projected_only if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Pod", "group": ""},
		"namespace": "chutes",
		"object": {
			"metadata": {
				"ownerReferences": [{"kind": "Job", "name": "failed-chute-cleanup-29577390"}],
			},
			"spec": {
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "cleanup", "image": "parachutes/failed-chute-cleanup:latest"}],
				"volumes": [
					{"name": "kube-api-access-bfm8f", "projected": {"sources": [
						{"serviceAccountToken": {"expirationSeconds": 3607, "path": "token"}},
						{"configMap": {"name": "kube-root-ca.crt", "items": [{"key": "ca.crt", "path": "ca.crt"}]}},
					]}},
				],
			},
		},
	}
	count({msg | deny[msg]; startswith(msg, "Chutes namespace: volume")}) == 0 with input as {"request": req}
}

# =============================================================================
# ALLOW: ConfigMap volume in system namespace (not restricted)
# =============================================================================

test_allow_configmap_volume_in_system_namespace if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Pod", "group": ""},
		"namespace": "kube-system",
		"object": {
			"metadata": {},
			"spec": {
				"containers": [{"name": "coredns", "image": "registry.k8s.io/coredns:v1.11.1"}],
				"volumes": [{"name": "config", "configMap": {"name": "coredns"}}],
			},
		},
	}
	not deny["Chutes namespace: volume 'config' uses a forbidden type (only hostPath, emptyDir, and projected allowed)"] with input as {"request": req}
}

# =============================================================================
# ALLOW: No volumes at all
# =============================================================================

test_chutes_allow_pod_with_no_volumes if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "Pod", "group": ""},
		"namespace": "chutes",
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"automountServiceAccountToken": false,
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1", "command": ["chutes", "run", "x:y"]}],
			},
		},
	}
	count({msg | deny[msg]; startswith(msg, "Chutes namespace: volume")}) == 0 with input as {"request": req}
}
