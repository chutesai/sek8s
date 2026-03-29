# OPA tests for hostPath volume restrictions (volumes.rego).
# Covers SEK8S-001, SEK8S-021, SEK8S-053 regression prevention.
# Run: make test-opa-policies
package kubernetes.admission

import future.keywords.if
import future.keywords.in

# =============================================================================
# SEK8S-001 regression: job-name label must NOT bypass hostPath restrictions
# =============================================================================

test_deny_pod_with_job_name_label_and_hostpath if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"job-name": "my-job"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "attack", "image": "busybox:latest"}],
				"volumes": [{"name": "root", "hostPath": {"path": "/"}}]
			}
		}
	}
	deny["hostPath volume '/' not allowed."] with input as {"request": req}
}

test_deny_pod_with_job_name_label_and_tmp_hostpath if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"job-name": "my-job"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "attack", "image": "busybox:latest"}],
				"volumes": [{"name": "tmp", "hostPath": {"path": "/tmp"}}]
			}
		}
	}
	deny["hostPath volume '/tmp' not allowed."] with input as {"request": req}
}

# =============================================================================
# SEK8S-021 regression: prefix-adjacent paths must be denied
# =============================================================================

test_deny_cache_evil_prefix_adjacent if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
				"volumes": [{"name": "evil", "hostPath": {"path": "/var/snap/cache-evil"}}]
			}
		}
	}
	deny["hostPath volume '/var/snap/cache-evil' not allowed."] with input as {"request": req}
}

# =============================================================================
# Cache hostPath: legitimate chute workloads allowed
# =============================================================================

test_allow_cache_hostpath_for_chute_pod if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
				"volumes": [
					{"name": "cache", "hostPath": {"path": "/var/snap/cache/abc"}},
					{"name": "base-cache", "hostPath": {"path": "/var/snap/cache"}}
				]
			}
		}
	}
	not deny["hostPath volume '/var/snap/cache/abc' not allowed."] with input as {"request": req}
	not deny["hostPath volume '/var/snap/cache' not allowed."] with input as {"request": req}
}

test_allow_cache_hostpath_for_chute_job if {
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
						"securityContext": {"runAsUser": 1000},
						"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1", "command": ["chutes", "run", "x:y"]}],
						"initContainers": [{"name": "cache-init", "image": "parachutes/cache-cleaner:latest", "securityContext": {"runAsUser": 0}}],
						"volumes": [{"name": "cache", "hostPath": {"path": "/var/snap/cache"}}]
					}
				}
			}
		}
	}
	not deny["Job hostPath volume '/var/snap/cache' not allowed. Use emptyDir for temporary storage."] with input as {"request": req}
}

# =============================================================================
# Cache hostPath: denied without chute label
# =============================================================================

test_deny_cache_hostpath_without_chute_label if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"app": "sneaky"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "attack", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
				"volumes": [{"name": "cache", "hostPath": {"path": "/var/snap/cache"}}]
			}
		}
	}
	deny["hostPath volume '/var/snap/cache' not allowed."] with input as {"request": req}
}

# =============================================================================
# Cache hostPath: denied with wrong image (not from validator registry)
# =============================================================================

test_deny_cache_hostpath_with_wrong_image if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "chute", "image": "docker.io/evil/image:latest"}],
				"volumes": [{"name": "cache", "hostPath": {"path": "/var/snap/cache"}}]
			}
		}
	}
	deny["hostPath volume '/var/snap/cache' not allowed."] with input as {"request": req}
}

test_deny_cache_hostpath_with_partial_registry_match if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "chute", "image": "evil.localregistry.chutes.ai.attacker.com/model:v1"}],
				"volumes": [{"name": "cache", "hostPath": {"path": "/var/snap/cache"}}]
			}
		}
	}
	deny["hostPath volume '/var/snap/cache' not allowed."] with input as {"request": req}
}

# =============================================================================
# Cache hostPath: denied in wrong namespace
# =============================================================================

test_deny_cache_hostpath_in_default_namespace if {
	req := {
		"operation": "CREATE",
		"namespace": "default",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"chutes/chute": "true"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "chute", "image": "testvalidator.localregistry.chutes.ai:30500/model:v1"}],
				"volumes": [{"name": "cache", "hostPath": {"path": "/var/snap/cache"}}]
			}
		}
	}
	deny["hostPath volume '/var/snap/cache' not allowed."] with input as {"request": req}
}

# =============================================================================
# SEK8S-053 regression: agent hostPath requires correct image
# =============================================================================

test_allow_agent_hostpath_with_correct_image if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"app.kubernetes.io/name": "agent"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "agent", "image": "parachutes/chutes-agent:k3s-latest"}],
				"volumes": [{"name": "state", "hostPath": {"path": "/var/lib/chutes/agent"}}]
			}
		}
	}
	not deny["hostPath volume '/var/lib/chutes/agent' not allowed."] with input as {"request": req}
}

test_deny_agent_hostpath_with_wrong_image if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"app.kubernetes.io/name": "agent"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "attack", "image": "busybox:latest"}],
				"volumes": [{"name": "state", "hostPath": {"path": "/var/lib/chutes/agent"}}]
			}
		}
	}
	deny["hostPath volume '/var/lib/chutes/agent' not allowed."] with input as {"request": req}
}

test_deny_agent_hostpath_without_label if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"app": "sneaky"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "agent", "image": "parachutes/chutes-agent:k3s-latest"}],
				"volumes": [{"name": "state", "hostPath": {"path": "/var/lib/chutes/agent"}}]
			}
		}
	}
	deny["hostPath volume '/var/lib/chutes/agent' not allowed."] with input as {"request": req}
}

test_allow_agent_deployment_with_correct_image if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Deployment"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {"labels": {"app.kubernetes.io/name": "agent"}},
					"spec": {
						"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
						"containers": [{"name": "agent", "image": "parachutes/chutes-agent:k3s-latest"}],
						"volumes": [{"name": "state", "hostPath": {"path": "/var/lib/chutes/agent"}}]
					}
				}
			}
		}
	}
	not deny["hostPath volume '/var/lib/chutes/agent' not allowed."] with input as {"request": req}
}

# =============================================================================
# General: arbitrary hostPath always denied
# =============================================================================

test_deny_arbitrary_hostpath_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "image": "busybox:latest"}],
				"volumes": [{"name": "etc", "hostPath": {"path": "/etc"}}]
			}
		}
	}
	deny["hostPath volume '/etc' not allowed."] with input as {"request": req}
}

test_deny_root_hostpath_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "image": "busybox:latest"}],
				"volumes": [{"name": "root", "hostPath": {"path": "/"}}]
			}
		}
	}
	deny["hostPath volume '/' not allowed."] with input as {"request": req}
}

# =============================================================================
# SEK8S-023: user-based exemptions replace namespace-based exclusions.
# Miner must be denied hostPath in ALL namespaces; system users exempt.
# =============================================================================

test_deny_miner_hostpath_in_kube_system if {
	req := {
		"operation": "CREATE",
		"namespace": "kube-system",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {}},
			"spec": {
				"containers": [{"name": "exploit", "image": "busybox:latest"}],
				"volumes": [{"name": "root", "hostPath": {"path": "/"}}]
			}
		},
		"userInfo": {"username": "miner"}
	}
	deny["hostPath volume '/' not allowed."] with input as {"request": req}
}

test_deny_miner_hostpath_in_monitoring if {
	req := {
		"operation": "CREATE",
		"namespace": "monitoring",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {}},
			"spec": {
				"containers": [{"name": "exploit", "image": "busybox:latest"}],
				"volumes": [{"name": "etc", "hostPath": {"path": "/etc"}}]
			}
		},
		"userInfo": {"username": "miner"}
	}
	deny["hostPath volume '/etc' not allowed."] with input as {"request": req}
}

test_allow_system_user_hostpath_in_kube_system if {
	req := {
		"operation": "CREATE",
		"namespace": "kube-system",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {}},
			"spec": {
				"containers": [{"name": "kube-proxy", "image": "rancher/mirrored-kube-proxy:v1.30.0"}],
				"volumes": [{"name": "lib-modules", "hostPath": {"path": "/lib/modules"}}]
			}
		},
		"userInfo": {"username": "system:serviceaccount:kube-system:kube-proxy"}
	}
	not deny["hostPath volume '/lib/modules' not allowed."] with input as {"request": req}
}

test_allow_monitoring_sa_hostpath if {
	req := {
		"operation": "CREATE",
		"namespace": "monitoring",
		"kind": {"kind": "DaemonSet"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {"labels": {}},
					"spec": {
						"containers": [{"name": "node-exporter", "image": "prom/node-exporter:v1.7.0"}],
						"volumes": [{"name": "proc", "hostPath": {"path": "/proc"}}]
					}
				}
			}
		},
		"userInfo": {"username": "system:serviceaccount:monitoring:prometheus-operator"}
	}
	not deny["hostPath volume '/proc' not allowed."] with input as {"request": req}
}

test_allow_system_masters_hostpath if {
	req := {
		"operation": "CREATE",
		"namespace": "kube-system",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {}},
			"spec": {
				"containers": [{"name": "debug", "image": "busybox"}],
				"volumes": [{"name": "host", "hostPath": {"path": "/var/log"}}]
			}
		},
		"userInfo": {"username": "kubernetes-admin", "groups": ["system:masters", "system:authenticated"]}
	}
	not deny["hostPath volume '/var/log' not allowed."] with input as {"request": req}
}

# Rollout restart (UPDATE) must not re-validate existing hostPath volumes.
# miner_restart.rego constrains UPDATE to restartedAt-only changes.
test_allow_miner_rollout_restart_daemonset_with_hostpath if {
	req := {
		"operation": "UPDATE",
		"namespace": "attestation-system",
		"kind": {"kind": "DaemonSet"},
		"object": {
			"spec": {
				"selector": {"matchLabels": {"app": "attestation-proxy"}},
				"template": {
					"metadata": {
						"labels": {"app": "attestation-proxy"},
						"annotations": {"kubectl.kubernetes.io/restartedAt": "2026-03-28T10:00:00Z"},
					},
					"spec": {
						"containers": [{"name": "proxy", "image": "parachutes/attestation-proxy:latest"}],
						"volumes": [
							{"name": "certs", "hostPath": {"path": "/etc/attestation-service/certs"}},
							{"name": "sock", "hostPath": {"path": "/run/attestation-service"}},
						],
					},
				},
			},
		},
		"oldObject": {
			"spec": {
				"selector": {"matchLabels": {"app": "attestation-proxy"}},
				"template": {
					"metadata": {"labels": {"app": "attestation-proxy"}},
					"spec": {
						"containers": [{"name": "proxy", "image": "parachutes/attestation-proxy:latest"}],
						"volumes": [
							{"name": "certs", "hostPath": {"path": "/etc/attestation-service/certs"}},
							{"name": "sock", "hostPath": {"path": "/run/attestation-service"}},
						],
					},
				},
			},
		},
		"userInfo": {"username": "miner"},
	}
	not deny["hostPath volume '/etc/attestation-service/certs' not allowed."] with input as {"request": req}
	not deny["hostPath volume '/run/attestation-service' not allowed."] with input as {"request": req}
}
