# OPA tests for the container environment-variable allowlist in pods.rego.
# A miner-submitted Pod may only set env vars in `allowed_env_vars`; everything
# else (and the explicit denylist) is rejected on CREATE and UPDATE.
# Run: make test-opa-policies
package kubernetes.admission

import future.keywords.if
import future.keywords.in

_miner_pod_req(op, env) := {
	"operation": op,
	"kind": {"kind": "Pod"},
	"namespace": "chutes",
	"userInfo": {"username": "miner"},
	"object": {
		"metadata": {"name": "chute-pod", "namespace": "chutes"},
		"spec": {"containers": [{"name": "app", "env": env}]},
	},
}

_env_denials(req) := {m | deny[m]; contains(m, "forbidden environment variable")}

# --- Allowed tuning env vars pass on CREATE and UPDATE ---

test_allow_hf_xet_concurrency_env_on_create if {
	req := _miner_pod_req("CREATE", [{"name": "HF_XET_FIXED_DOWNLOAD_CONCURRENCY", "value": "16"}])
	count(_env_denials(req)) == 0 with input as {"request": req}
}

test_allow_tokio_worker_threads_env_on_update if {
	req := _miner_pod_req("UPDATE", [{"name": "TOKIO_WORKER_THREADS", "value": "8"}])
	count(_env_denials(req)) == 0 with input as {"request": req}
}

# --- Explicitly forbidden names are denied ---

test_deny_kube_token_env if {
	req := _miner_pod_req("CREATE", [{"name": "KUBE_TOKEN", "value": "x"}])
	count(_env_denials(req)) > 0 with input as {"request": req}
}

test_deny_kubeconfig_env if {
	req := _miner_pod_req("CREATE", [{"name": "KUBECONFIG", "value": "/tmp/kc"}])
	count(_env_denials(req)) > 0 with input as {"request": req}
}

# --- Any name not in the allowlist is denied (default-deny) ---

test_deny_unlisted_env_on_create if {
	req := _miner_pod_req("CREATE", [{"name": "EVIL_VAR", "value": "x"}])
	count(_env_denials(req)) > 0 with input as {"request": req}
}

test_deny_unlisted_env_on_update if {
	req := _miner_pod_req("UPDATE", [{"name": "EVIL_VAR", "value": "x"}])
	count(_env_denials(req)) > 0 with input as {"request": req}
}
