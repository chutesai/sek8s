# OPA tests for SEK8S-005 (effective_deny / template check gaps) and
# SEK8S-006 (initContainer / ephemeralContainer capability gaps).
# Run: ./bin/opa test ansible/k3s/roles/admission-controller/files/policies tests/opa -v
package kubernetes.admission

import future.keywords.if
import future.keywords.in

# =============================================================================
# SEK8S-005: Deployment template must be checked for hostNetwork/hostPID/hostIPC
# Currently Pod-only in pods.rego; miner can bypass via Deployment.
# =============================================================================

test_deny_deployment_with_hostnetwork_in_template if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Deployment"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {},
					"spec": {
						"hostNetwork": true,
						"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
						"containers": [{"name": "app", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "host network")}) > 0 with input as {"request": req}
}

test_deny_deployment_with_hostpid_in_template if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Deployment"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {},
					"spec": {
						"hostPID": true,
						"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
						"containers": [{"name": "app", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "host PID")}) > 0 with input as {"request": req}
}

test_deny_deployment_with_hostipc_in_template if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Deployment"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {},
					"spec": {
						"hostIPC": true,
						"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
						"containers": [{"name": "app", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "host IPC")}) > 0 with input as {"request": req}
}

test_deny_job_with_hostnetwork_in_template if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Job"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {"labels": {"chutes/chute": "true"}},
					"spec": {
						"hostNetwork": true,
						"securityContext": {"runAsUser": 1000},
						"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1", "command": ["chutes", "run", "x:y"]}],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "host network")}) > 0 with input as {"request": req}
}

# =============================================================================
# SEK8S-006: initContainer and ephemeralContainer capability checks
# Currently only regular containers are checked in pods.rego.
# =============================================================================

test_deny_pod_initcontainer_with_dangerous_capability if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
				"initContainers": [{"name": "exploit", "image": "busybox", "securityContext": {"runAsUser": 1000, "capabilities": {"add": ["SYS_MODULE"]}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "dangerous capability")}) > 0 with input as {"request": req}
}

test_deny_pod_ephemeralcontainer_with_dangerous_capability if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
				"ephemeralContainers": [{"name": "debug", "image": "busybox", "securityContext": {"capabilities": {"add": ["SYS_ADMIN"]}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "dangerous capability")}) > 0 with input as {"request": req}
}

test_deny_deployment_initcontainer_with_dangerous_capability if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Deployment"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {},
					"spec": {
						"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
						"containers": [{"name": "app", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
						"initContainers": [{"name": "exploit", "image": "busybox", "securityContext": {"runAsUser": 1000, "capabilities": {"add": ["SYS_MODULE"]}}}],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "dangerous capability")}) > 0 with input as {"request": req}
}

# =============================================================================
# SEK8S-010: Capability check must block both SYS_ADMIN (k8s format) and
# CAP_SYS_ADMIN (legacy prefix). Prior to fix, only CAP_ prefixed was checked
# meaning the standard k8s format bypassed the policy entirely.
# =============================================================================

test_deny_pod_capability_k8s_format if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "exploit", "image": "busybox", "securityContext": {"capabilities": {"add": ["SYS_ADMIN"]}}, "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "dangerous capability")}) > 0 with input as {"request": req}
}

test_deny_pod_capability_cap_prefix_format if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "exploit", "image": "busybox", "securityContext": {"capabilities": {"add": ["CAP_SYS_MODULE"]}}, "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "dangerous capability")}) > 0 with input as {"request": req}
}

test_allow_pod_safe_capability if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "image": "busybox", "securityContext": {"capabilities": {"add": ["NET_BIND_SERVICE"]}}, "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "dangerous capability")}) == 0 with input as {"request": req}
}

# =============================================================================
# SEK8S-014: allowPrivilegeEscalation must be blocked on all resource types
# and all container types (containers, initContainers, ephemeralContainers).
# Prior to fix, only template-based Deployment/StatefulSet/DaemonSet/ReplicaSet
# containers were checked — direct Pods, Jobs, and init/ephemeral containers
# were not.
# =============================================================================

test_deny_pod_allow_privilege_escalation if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "exploit", "image": "busybox", "securityContext": {"allowPrivilegeEscalation": true}, "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "privilege escalation")}) > 0 with input as {"request": req}
}

test_deny_pod_initcontainer_allow_privilege_escalation if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
				"initContainers": [{"name": "exploit", "image": "busybox", "securityContext": {"allowPrivilegeEscalation": true}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "privilege escalation")}) > 0 with input as {"request": req}
}

test_deny_pod_ephemeralcontainer_allow_privilege_escalation if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
				"ephemeralContainers": [{"name": "debug", "image": "busybox", "securityContext": {"allowPrivilegeEscalation": true}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "privilege escalation")}) > 0 with input as {"request": req}
}

test_deny_job_allow_privilege_escalation if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Job"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {},
					"spec": {
						"securityContext": {"runAsUser": 1000},
						"containers": [{"name": "exploit", "image": "busybox", "securityContext": {"allowPrivilegeEscalation": true}}],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "privilege escalation")}) > 0 with input as {"request": req}
}

test_deny_deployment_initcontainer_allow_privilege_escalation if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Deployment"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {},
					"spec": {
						"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
						"containers": [{"name": "app", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
						"initContainers": [{"name": "exploit", "image": "busybox", "securityContext": {"allowPrivilegeEscalation": true}}],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "privilege escalation")}) > 0 with input as {"request": req}
}

test_deny_deployment_ephemeralcontainer_allow_privilege_escalation if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Deployment"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {},
					"spec": {
						"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
						"containers": [{"name": "app", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
						"ephemeralContainers": [{"name": "debug", "image": "busybox", "securityContext": {"allowPrivilegeEscalation": true}}],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "privilege escalation")}) > 0 with input as {"request": req}
}

test_allow_pod_without_privilege_escalation if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "image": "busybox", "securityContext": {"allowPrivilegeEscalation": false}, "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "privilege escalation")}) == 0 with input as {"request": req}
}

# =============================================================================
# SEK8S-005 / miner_restart: miner CAN restart any Deployment/DaemonSet
# =============================================================================

test_allow_miner_restart_deployment_outside_chutes if {
	req := {
		"namespace": "attestation-system",
		"operation": "UPDATE",
		"kind": {"kind": "Deployment"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"app": "proxy"}},
				"template": {
					"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-03-25T10:00:00Z"}},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"app": "proxy"}},
				"template": {
					"metadata": {},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	not deny["Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"] with input as {"request": req}
}

test_allow_miner_restart_daemonset_in_kube_system if {
	req := {
		"namespace": "kube-system",
		"operation": "UPDATE",
		"kind": {"kind": "DaemonSet"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-03-25T10:00:00Z"}},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	not deny["Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"] with input as {"request": req}
}

# =============================================================================
# SEK8S-005 / miner_restart: miner CANNOT modify other properties
# =============================================================================

test_deny_miner_inject_annotation_alongside_restart_outside_chutes if {
	req := {
		"namespace": "attestation-system",
		"operation": "UPDATE",
		"kind": {"kind": "Deployment"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"app": "proxy"}},
				"template": {
					"metadata": {
						"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-03-25T10:00:00Z", "extra": "injected"},
					},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"app": "proxy"}},
				"template": {
					"metadata": {},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	deny["Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"] with input as {"request": req}
}

test_deny_miner_inject_volume_alongside_restart_outside_chutes if {
	req := {
		"namespace": "attestation-system",
		"operation": "UPDATE",
		"kind": {"kind": "Deployment"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"app": "proxy"}},
				"template": {
					"metadata": {
						"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-03-25T10:00:00Z"},
					},
					"spec": {
						"containers": [{"name": "proxy", "image": "original:v1"}],
						"volumes": [{"name": "root", "hostPath": {"path": "/"}}],
					},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"app": "proxy"}},
				"template": {
					"metadata": {},
					"spec": {
						"containers": [{"name": "proxy", "image": "original:v1"}],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	deny["Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"] with input as {"request": req}
}

test_deny_miner_inject_privileged_alongside_restart_outside_chutes if {
	req := {
		"namespace": "kube-system",
		"operation": "UPDATE",
		"kind": {"kind": "DaemonSet"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {
						"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-03-25T10:00:00Z"},
					},
					"spec": {
						"containers": [{"name": "coredns", "image": "rancher/mirrored-coredns-coredns:1.11.4", "securityContext": {"privileged": true}}],
					},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {},
					"spec": {
						"containers": [{"name": "coredns", "image": "rancher/mirrored-coredns-coredns:1.11.4"}],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	deny["Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"] with input as {"request": req}
}

test_deny_miner_modify_deployment_without_restart_outside_chutes if {
	req := {
		"namespace": "attestation-system",
		"operation": "UPDATE",
		"kind": {"kind": "Deployment"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"app": "proxy"}},
				"template": {
					"metadata": {"annotations": {"custom": "injected"}},
					"spec": {"containers": [{"name": "proxy", "image": "evil:latest"}]},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"app": "proxy"}},
				"template": {
					"metadata": {},
					"spec": {"containers": [{"name": "proxy", "image": "original:v1"}]},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	deny["Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"] with input as {"request": req}
}

# No-op patch where restartedAt already exists but didn't change — must be
# denied because the intent is not a rollout restart.
test_deny_miner_noop_patch_with_stale_restarted_at if {
	req := {
		"namespace": "kube-system",
		"operation": "UPDATE",
		"kind": {"kind": "Deployment"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {
						"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-03-28T22:58:03-04:00", "sec-test": "true"},
					},
					"spec": {"containers": [{"name": "coredns", "image": "rancher/mirrored-coredns-coredns:1.13.1"}]},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {
						"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-03-28T22:58:03-04:00", "sec-test": "true"},
					},
					"spec": {"containers": [{"name": "coredns", "image": "rancher/mirrored-coredns-coredns:1.13.1"}]},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	deny["Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"] with input as {"request": req}
}

# Subsequent rollout restart on a deployment that already has restartedAt
# from a previous restart — allowed because the timestamp changed.
test_allow_miner_re_restart_with_new_timestamp if {
	req := {
		"namespace": "kube-system",
		"operation": "UPDATE",
		"kind": {"kind": "Deployment"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {
						"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-03-29T04:00:00Z"},
					},
					"spec": {"containers": [{"name": "coredns", "image": "rancher/mirrored-coredns-coredns:1.13.1"}]},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {
						"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-03-28T22:58:03-04:00"},
					},
					"spec": {"containers": [{"name": "coredns", "image": "rancher/mirrored-coredns-coredns:1.13.1"}]},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	not deny["Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"] with input as {"request": req}
}

# =============================================================================
# SEK8S-005 / miner_restart: system controller CAN fully update
# =============================================================================

test_allow_system_controller_full_update_outside_chutes if {
	req := {
		"namespace": "gpu-operator",
		"operation": "UPDATE",
		"kind": {"kind": "DaemonSet"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"app": "gpu-driver"}},
				"template": {
					"metadata": {"labels": {"app": "gpu-driver", "version": "v2"}},
					"spec": {"containers": [{"name": "driver", "image": "nvidia/driver:580"}]},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"app": "gpu-driver"}},
				"template": {
					"metadata": {"labels": {"app": "gpu-driver", "version": "v1"}},
					"spec": {"containers": [{"name": "driver", "image": "nvidia/driver:535"}]},
				},
			},
		},
		"userInfo": {"username": "system:serviceaccount:gpu-operator:gpu-operator"},
	}
	not deny["Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"] with input as {"request": req}
}

# =============================================================================
# SEK8S-023: kube-system namespace must be policy-enforced.
# The webhook previously excluded kube-system via namespaceSelector, creating a
# blind spot where the miner had unrestricted patch on deployments/daemonsets.
# =============================================================================

test_deny_miner_arbitrary_patch_deployment_kube_system if {
	req := {
		"namespace": "kube-system",
		"operation": "UPDATE",
		"kind": {"kind": "Deployment"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {"annotations": {"sec-test": "true"}},
					"spec": {"containers": [{"name": "coredns", "image": "rancher/mirrored-coredns-coredns:1.11.4"}]},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {},
					"spec": {"containers": [{"name": "coredns", "image": "rancher/mirrored-coredns-coredns:1.11.4"}]},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	deny["Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"] with input as {"request": req}
}

test_allow_system_controller_update_deployment_kube_system if {
	req := {
		"namespace": "kube-system",
		"operation": "UPDATE",
		"kind": {"kind": "Deployment"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {"labels": {"k8s-app": "kube-dns", "updated": "true"}},
					"spec": {"containers": [{"name": "coredns", "image": "rancher/mirrored-coredns-coredns:1.12.0"}]},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"k8s-app": "kube-dns"}},
				"template": {
					"metadata": {"labels": {"k8s-app": "kube-dns"}},
					"spec": {"containers": [{"name": "coredns", "image": "rancher/mirrored-coredns-coredns:1.11.4"}]},
				},
			},
		},
		"userInfo": {"username": "system:serviceaccount:kube-system:deployment-controller"},
	}
	not deny["Outside chutes namespace, PATCH on Deployment/DaemonSet may only change spec.template.metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]"] with input as {"request": req}
}

test_allow_privileged_ephemeral_container_for_system_user if {
	req := {
		"operation": "CREATE",
		"namespace": "kube-system",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"containers": [{"name": "coredns", "image": "rancher/mirrored-coredns-coredns:1.11.4"}],
				"ephemeralContainers": [{"name": "debug", "image": "busybox", "securityContext": {"privileged": true}}],
			},
		},
		"userInfo": {"username": "system:serviceaccount:kube-system:node-controller"},
	}
	count({m | deny[m]; contains(m, "privileged security context")}) == 0 with input as {"request": req}
}

# =============================================================================
# SEK8S-023: user-based exemptions replace namespace-based exclusions.
# Miner must be denied in ALL namespaces including kube-system.
# System/controller users and system:masters are exempt.
# =============================================================================

test_deny_miner_privileged_pod_in_kube_system if {
	req := {
		"operation": "CREATE",
		"namespace": "kube-system",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"containers": [{"name": "exploit", "image": "busybox", "securityContext": {"privileged": true}, "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "privileged security context")}) > 0 with input as {"request": req}
}

test_deny_miner_hostnetwork_pod_in_kube_system if {
	req := {
		"operation": "CREATE",
		"namespace": "kube-system",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"hostNetwork": true,
				"containers": [{"name": "exploit", "image": "busybox", "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "host network")}) > 0 with input as {"request": req}
}

test_deny_miner_dangerous_capability_in_gpu_operator if {
	req := {
		"operation": "CREATE",
		"namespace": "gpu-operator",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"containers": [{"name": "exploit", "image": "busybox", "securityContext": {"capabilities": {"add": ["SYS_ADMIN"]}}, "resources": {"limits": {"memory": "1Gi"}}}],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "dangerous capability")}) > 0 with input as {"request": req}
}

test_allow_gpu_operator_sa_privileged_pod if {
	req := {
		"operation": "CREATE",
		"namespace": "gpu-operator",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"containers": [{"name": "driver", "image": "nvcr.io/nvidia/driver:535", "securityContext": {"privileged": true}}],
			},
		},
		"userInfo": {"username": "system:serviceaccount:gpu-operator:gpu-operator"},
	}
	count({m | deny[m]; contains(m, "privileged security context")}) == 0 with input as {"request": req}
}

test_allow_system_masters_privileged_pod if {
	req := {
		"operation": "CREATE",
		"namespace": "kube-system",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"containers": [{"name": "debug", "image": "busybox", "securityContext": {"privileged": true}}],
			},
		},
		"userInfo": {"username": "kubernetes-admin", "groups": ["system:masters", "system:authenticated"]},
	}
	count({m | deny[m]; contains(m, "privileged security context")}) == 0 with input as {"request": req}
}

test_deny_miner_exec_in_kube_system if {
	req := {
		"namespace": "kube-system",
		"kind": {"kind": "PodExecOptions"},
		"userInfo": {"username": "miner"},
	}
	deny["Pod exec operations are not permitted."] with input as {"request": req}
}

test_allow_system_user_exec_in_kube_system if {
	req := {
		"namespace": "kube-system",
		"kind": {"kind": "PodExecOptions"},
		"userInfo": {"username": "system:serviceaccount:kube-system:node-controller"},
	}
	not deny["Pod exec operations are not permitted."] with input as {"request": req}
}

test_deny_miner_portforward_in_kube_system if {
	req := {
		"namespace": "kube-system",
		"kind": {"kind": "PodPortForwardOptions"},
		"userInfo": {"username": "miner"},
	}
	deny["Pod port forward operations are not permitted."] with input as {"request": req}
}

test_deny_miner_attach_in_kube_system if {
	req := {
		"namespace": "kube-system",
		"kind": {"kind": "PodAttachOptions"},
		"userInfo": {"username": "miner"},
	}
	deny["Pod attach operations are not permitted."] with input as {"request": req}
}

test_allow_system_masters_exec if {
	req := {
		"namespace": "chutes",
		"kind": {"kind": "PodExecOptions"},
		"userInfo": {"username": "kubernetes-admin", "groups": ["system:masters", "system:authenticated"]},
	}
	not deny["Pod exec operations are not permitted."] with input as {"request": req}
}

# Rollout restart (UPDATE) must not re-validate existing privileged containers.
# miner_restart.rego constrains UPDATE to restartedAt-only changes.
test_allow_miner_rollout_restart_privileged_daemonset if {
	req := {
		"operation": "UPDATE",
		"namespace": "gpu-operator",
		"kind": {"kind": "DaemonSet"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"app": "gpu-driver"}},
				"template": {
					"metadata": {
						"labels": {"app": "gpu-driver"},
						"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-03-28T10:00:00Z"},
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
						"hostNetwork": true,
						"containers": [{"name": "driver", "image": "nvcr.io/nvidia/driver:535", "securityContext": {"privileged": true, "capabilities": {"add": ["SYS_ADMIN"]}}}],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "privileged")}) == 0 with input as {"request": req}
	count({m | deny[m]; contains(m, "host network")}) == 0 with input as {"request": req}
	count({m | deny[m]; contains(m, "dangerous capability")}) == 0 with input as {"request": req}
}
