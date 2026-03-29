# OPA tests for rbac.rego RBAC and admission webhook lockdown.
# Run: ./bin/opa test ansible/k3s/roles/admission-controller/files/policies tests/opa -v
package kubernetes.admission

import future.keywords.if

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_rbac_request(operation, kind, name, username, groups) := req if {
	req := {
		"operation": operation,
		"kind": {"kind": kind, "group": "rbac.authorization.k8s.io"},
		"name": name,
		"userInfo": {"username": username, "groups": groups},
	}
}

# ---------------------------------------------------------------------------
# RBAC lockdown: miner denied
# ---------------------------------------------------------------------------

test_deny_miner_update_clusterrole if {
	req := _rbac_request("UPDATE", "ClusterRole", "miner", "miner", ["system:authenticated"])
	count(deny) > 0 with input as {"request": req}
}

test_deny_miner_create_clusterrolebinding if {
	req := _rbac_request("CREATE", "ClusterRoleBinding", "escalation", "miner", ["system:authenticated"])
	count(deny) > 0 with input as {"request": req}
}

test_deny_miner_delete_role if {
	req := _rbac_request("DELETE", "Role", "some-role", "miner", ["system:authenticated"])
	count(deny) > 0 with input as {"request": req}
}

# ---------------------------------------------------------------------------
# RBAC lockdown: system:masters allowed (Helm upgrades)
# ---------------------------------------------------------------------------

test_allow_system_masters_update_clusterrole if {
	req := _rbac_request("UPDATE", "ClusterRole", "miner", "system:admin", ["system:masters", "system:authenticated"])
	count(deny) == 0 with input as {"request": req}
}

test_allow_system_masters_create_clusterrolebinding if {
	req := _rbac_request("CREATE", "ClusterRoleBinding", "miner-binding", "system:admin", ["system:masters", "system:authenticated"])
	count(deny) == 0 with input as {"request": req}
}

# ---------------------------------------------------------------------------
# RBAC lockdown: existing exemptions still work
# ---------------------------------------------------------------------------

test_allow_k3s_supervisor_update_clusterrole if {
	req := _rbac_request("UPDATE", "ClusterRole", "system:controller", "system:k3s-supervisor", ["system:authenticated"])
	count(deny) == 0 with input as {"request": req}
}

test_allow_gpu_operator_sa_create_role if {
	req := _rbac_request("CREATE", "Role", "gpu-role", "system:serviceaccount:gpu-operator:gpu-operator", ["system:serviceaccounts"])
	count(deny) == 0 with input as {"request": req}
}

test_allow_kube_system_sa_update_rolebinding if {
	req := _rbac_request("UPDATE", "RoleBinding", "coredns", "system:serviceaccount:kube-system:coredns", ["system:serviceaccounts"])
	count(deny) == 0 with input as {"request": req}
}

# ---------------------------------------------------------------------------
# Admission webhook lockdown
# ---------------------------------------------------------------------------

test_deny_miner_create_validating_webhook if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "ValidatingWebhookConfiguration", "group": "admissionregistration.k8s.io"},
		"name": "evil-webhook",
		"userInfo": {"username": "miner", "groups": ["system:authenticated"]},
	}
	count(deny) > 0 with input as {"request": req}
}

test_deny_miner_delete_admission_controller_webhook if {
	req := {
		"operation": "DELETE",
		"kind": {"kind": "ValidatingWebhookConfiguration", "group": "admissionregistration.k8s.io"},
		"name": "admission-controller-webhook",
		"userInfo": {"username": "miner", "groups": ["system:authenticated"]},
	}
	count(deny) > 0 with input as {"request": req}
}
