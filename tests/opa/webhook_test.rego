# OPA tests for webhook.rego k3s-config ConfigMap protection.
# With kube-system included in webhook scope, the miner must be blocked from
# modifying k3s-config while system controllers remain unblocked.
# Run: ./bin/opa test ansible/guest/roles/admission-controller/files/policies tests/opa -v
package kubernetes.admission

import future.keywords.if

test_deny_miner_update_k3s_config if {
	req := {
		"operation": "UPDATE",
		"namespace": "kube-system",
		"kind": {"kind": "ConfigMap"},
		"name": "k3s-config",
		"object": {
			"metadata": {"name": "k3s-config", "namespace": "kube-system"},
			"data": {"config": "modified"},
		},
		"userInfo": {"username": "miner"},
	}
	deny["K3s configuration cannot be modified at runtime"] with input as {"request": req}
}

test_allow_system_controller_update_k3s_config if {
	req := {
		"operation": "UPDATE",
		"namespace": "kube-system",
		"kind": {"kind": "ConfigMap"},
		"name": "k3s-config",
		"object": {
			"metadata": {"name": "k3s-config", "namespace": "kube-system"},
			"data": {"config": "updated-by-k3s"},
		},
		"userInfo": {"username": "system:k3s-supervisor"},
	}
	not deny["K3s configuration cannot be modified at runtime"] with input as {"request": req}
}
