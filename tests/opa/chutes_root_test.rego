# OPA tests for chutes namespace root-denial policy and the single cache-init exception.
# Run locally: make test-opa-policies (or: opa test <policies-dir> tests/opa -v)
package kubernetes.admission

import future.keywords.if
import future.keywords.in

# =============================================================================
# Unit tests: chutes_is_cache_cleaner_init
# =============================================================================

test_chutes_is_cache_cleaner_init_true_when_name_and_image_match if {
	container := {"name": "cache-init", "image": "parachutes/cache-cleaner:release-next-latest"}
	chutes_is_cache_cleaner_init(container)
}

test_chutes_is_cache_cleaner_init_false_when_name_wrong if {
	container := {"name": "other-init", "image": "parachutes/cache-cleaner:tag"}
	not chutes_is_cache_cleaner_init(container)
}

test_chutes_is_cache_cleaner_init_false_when_image_wrong if {
	container := {"name": "cache-init", "image": "busybox:latest"}
	not chutes_is_cache_cleaner_init(container)
}

# =============================================================================
# Unit tests: chutes_container_runs_root_denied
# =============================================================================

test_deny_main_container_root if {
	container := {"name": "app", "securityContext": {"runAsUser": 0}}
	pod_spec := {"securityContext": {"runAsUser": 1000}}
	chutes_container_runs_root_denied(container, pod_spec, false)
}

test_deny_ephemeral_container_root if {
	container := {"name": "debug", "securityContext": {"runAsUser": 0}}
	pod_spec := {"securityContext": {"runAsUser": 1000}}
	chutes_container_runs_root_denied(container, pod_spec, false)
}

test_allow_init_cache_cleaner_root if {
	container := {"name": "cache-init", "image": "parachutes/cache-cleaner:tag", "securityContext": {"runAsUser": 0}}
	pod_spec := {"securityContext": {"runAsUser": 1000}}
	not chutes_container_runs_root_denied(container, pod_spec, true)
}

test_deny_init_other_than_cache_cleaner_root if {
	container := {"name": "other-init", "image": "busybox", "securityContext": {"runAsUser": 0}}
	pod_spec := {"securityContext": {"runAsUser": 1000}}
	chutes_container_runs_root_denied(container, pod_spec, true)
}

test_deny_init_cache_cleaner_image_but_wrong_name_root if {
	container := {"name": "other-init", "image": "parachutes/cache-cleaner:tag", "securityContext": {"runAsUser": 0}}
	pod_spec := {"securityContext": {"runAsUser": 1000}}
	chutes_container_runs_root_denied(container, pod_spec, true)
}

test_allow_non_root_container if {
	container := {"name": "app", "securityContext": {"runAsUser": 1000}}
	pod_spec := {"securityContext": {"runAsUser": 1000}}
	not chutes_container_runs_root_denied(container, pod_spec, false)
}

# =============================================================================
# Integration tests: full admission request (deny set)
# =============================================================================

test_deny_pod_level_run_as_user_zero if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 0},
				"containers": [{"name": "app"}]
			}
		}
	}
	count(deny) > 0 with input as {"request": req}
	deny["Chutes namespace: pods must not run as root (runAsUser: 0)"] with input as {"request": req}
}

test_deny_pod_run_as_user_unspecified if {
	# No securityContext.runAsUser: would allow image default (often root)
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"containers": [{"name": "app"}]
			}
		}
	}
	deny["Chutes namespace: pod spec must set securityContext.runAsUser to a non-zero value"] with input as {"request": req}
}

test_deny_pod_main_container_run_as_user_zero if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app", "securityContext": {"runAsUser": 0}}]
			}
		}
	}
	deny["Chutes namespace: container 'app' must not run as root (runAsUser: 0)"] with input as {"request": req}
}

test_deny_pod_init_container_root_not_cache_cleaner if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app"}],
				"initContainers": [{"name": "other-init", "image": "busybox", "securityContext": {"runAsUser": 0}}]
			}
		}
	}
	count(deny) > 0 with input as {"request": req}
	deny[_] == "Chutes namespace: init container 'other-init' must not run as root (runAsUser: 0)" with input as {"request": req}
}

test_deny_job_init_cache_cleaner_image_wrong_name_root if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Job"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"spec": {
						"securityContext": {"runAsUser": 1000},
						"containers": [{"name": "chute", "command": ["chutes", "run", "x:y"]}],
						"initContainers": [{"name": "other-init", "image": "parachutes/cache-cleaner:tag", "securityContext": {"runAsUser": 0}}]
					}
				}
			}
		}
	}
	deny["Chutes namespace: init container 'other-init' must not run as root (runAsUser: 0)"] with input as {"request": req}
}

test_allow_job_with_cache_init_root if {
	# Minimal Job (chute workload): cache-init runAsUser 0, main chute non-root; exempt from runAsNonRoot
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
						"containers": [{"name": "chute", "command": ["chutes", "run", "x:y"]}],
						"initContainers": [{"name": "cache-init", "image": "parachutes/cache-cleaner:release-next-latest", "securityContext": {"runAsUser": 0}}],
						"volumes": [
							{"name": "cache", "hostPath": {"path": "/var/snap/cache/abc", "type": "DirectoryOrCreate"}},
							{"name": "raw-cache", "hostPath": {"path": "/var/snap/cache", "type": "DirectoryOrCreate"}},
							{"name": "tmp", "emptyDir": {}},
							{"name": "code", "configMap": {"name": "dummy"}}
						]
					}
				}
			}
		}
	}
	# Cache-init root must not be denied (it is the only allowed root exception)
	not deny["Chutes namespace: init container 'cache-init' must not run as root (runAsUser: 0)"] with input as {"request": req}
	# Chute workload is exempt from runAsNonRoot
	not deny["Chutes namespace: pod spec must set securityContext.runAsNonRoot: true (chute workloads excepted)"] with input as {"request": req}
}

# =============================================================================
# runAsNonRoot: non-chute workloads must set it; chute workloads exempt
# =============================================================================

test_deny_non_chute_pod_without_run_as_non_root if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"app": "other"}},
			"spec": {
				"securityContext": {"runAsUser": 1000},
				"containers": [{"name": "app"}]
			}
		}
	}
	deny["Chutes namespace: pod spec must set securityContext.runAsNonRoot: true (chute workloads excepted)"] with input as {"request": req}
}

test_allow_non_chute_pod_with_run_as_non_root if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Pod"},
		"object": {
			"metadata": {"labels": {"app": "other"}},
			"spec": {
				"securityContext": {"runAsUser": 1000, "runAsNonRoot": true},
				"containers": [{"name": "app"}]
			}
		}
	}
	not deny["Chutes namespace: pod spec must set securityContext.runAsNonRoot: true (chute workloads excepted)"] with input as {"request": req}
}

test_allow_chute_job_without_run_as_non_root if {
	# Chute Job (label chutes/chute=true) may omit runAsNonRoot so cache-init can run as root
	req := {
		"namespace": "chutes",
		"kind": {"kind": "Job"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {"labels": {"chutes/chute": "true"}},
					"spec": {
						"securityContext": {"runAsUser": 1000},
						"containers": [{"name": "chute", "command": ["chutes", "run", "x:y"]}],
						"initContainers": [{"name": "cache-init", "image": "parachutes/cache-cleaner:tag", "securityContext": {"runAsUser": 0}}]
					}
				}
			}
		}
	}
	not deny["Chutes namespace: pod spec must set securityContext.runAsNonRoot: true (chute workloads excepted)"] with input as {"request": req}
}

test_deny_deployment_with_chute_label_without_run_as_non_root if {
	# Only Job (and resulting Pod) are chute workloads; Deployment with the label is not exempt
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Deployment"},
		"object": {
			"metadata": {},
			"spec": {
				"template": {
					"metadata": {"labels": {"chutes/chute": "true", "app": "fake"}},
					"spec": {
						"securityContext": {"runAsUser": 1000},
						"containers": [{"name": "app"}]
					}
				}
			}
		}
	}
	deny["Chutes namespace: pod spec must set securityContext.runAsNonRoot: true (chute workloads excepted)"] with input as {"request": req}
}
