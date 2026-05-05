# OPA tests for crd.rego CRD mutation lockdown.
# Run: ./bin/opa test ansible/guest/roles/admission-controller/files/policies tests/opa -v
package kubernetes.admission

import future.keywords.if

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_crd_request(operation, crd_name, username) := req if {
	req := {
		"operation": operation,
		"kind": {"kind": "CustomResourceDefinition"},
		"name": crd_name,
		"userInfo": {"username": username, "groups": ["system:serviceaccounts"]},
	}
}

# ---------------------------------------------------------------------------
# Miner blocked from CRD mutations
# ---------------------------------------------------------------------------

test_deny_miner_create_crd if {
	req := _crd_request("CREATE", "evil.example.com", "miner")
	deny["CRD operation 'CREATE' on 'evil.example.com' is not allowed"] with input as {"request": req}
}

test_deny_miner_update_crd if {
	req := _crd_request("UPDATE", "clusterpolicies.nvidia.com", "miner")
	deny["CRD operation 'UPDATE' on 'clusterpolicies.nvidia.com' is not allowed"] with input as {"request": req}
}

test_deny_miner_delete_crd if {
	req := _crd_request("DELETE", "clusterpolicies.nvidia.com", "miner")
	deny["CRD operation 'DELETE' on 'clusterpolicies.nvidia.com' is not allowed"] with input as {"request": req}
}

# ---------------------------------------------------------------------------
# GPU operator SA allowed to manage NVIDIA CRDs
# ---------------------------------------------------------------------------

test_allow_gpu_operator_update_nvidia_crd if {
	req := _crd_request("UPDATE", "clusterpolicies.nvidia.com", "system:serviceaccount:gpu-operator:gpu-operator")
	count({m | deny[m]; contains(m, "CRD operation")}) == 0 with input as {"request": req}
}

test_allow_gpu_operator_update_nvidiadrivers_crd if {
	req := _crd_request("UPDATE", "nvidiadrivers.nvidia.com", "system:serviceaccount:gpu-operator:gpu-operator")
	count({m | deny[m]; contains(m, "CRD operation")}) == 0 with input as {"request": req}
}

test_allow_gpu_operator_create_nvidia_crd if {
	req := _crd_request("CREATE", "clusterpolicies.nvidia.com", "system:serviceaccount:gpu-operator:gpu-operator")
	count({m | deny[m]; contains(m, "CRD operation")}) == 0 with input as {"request": req}
}

test_deny_gpu_operator_non_nvidia_crd if {
	req := _crd_request("CREATE", "evil.example.com", "system:serviceaccount:gpu-operator:gpu-operator")
	deny["CRD operation 'CREATE' on 'evil.example.com' is not allowed"] with input as {"request": req}
}

# ---------------------------------------------------------------------------
# GPU operator SA allowed to manage bundled NFD CRDs (gpu-operator v26+)
# ---------------------------------------------------------------------------

test_allow_gpu_operator_update_nfd_crd if {
	req := _crd_request("UPDATE", "nodefeatures.nfd.k8s-sigs.io", "system:serviceaccount:gpu-operator:gpu-operator-upgrade-crd")
	count({m | deny[m]; contains(m, "CRD operation")}) == 0 with input as {"request": req}
}

test_allow_gpu_operator_update_nodefeaturerules_crd if {
	req := _crd_request("UPDATE", "nodefeaturerules.nfd.k8s-sigs.io", "system:serviceaccount:gpu-operator:gpu-operator")
	count({m | deny[m]; contains(m, "CRD operation")}) == 0 with input as {"request": req}
}

test_allow_gpu_operator_create_nfd_crd if {
	req := _crd_request("CREATE", "nodefeaturegroups.nfd.k8s-sigs.io", "system:serviceaccount:gpu-operator:gpu-operator")
	count({m | deny[m]; contains(m, "CRD operation")}) == 0 with input as {"request": req}
}

# Non-nvidia, non-NFD CRDs are still denied even for gpu-operator SAs
test_deny_gpu_operator_arbitrary_non_bundled_crd if {
	req := _crd_request("CREATE", "widgets.example.com", "system:serviceaccount:gpu-operator:gpu-operator")
	deny["CRD operation 'CREATE' on 'widgets.example.com' is not allowed"] with input as {"request": req}
}

# ---------------------------------------------------------------------------
# K3s system exemptions still work
# ---------------------------------------------------------------------------

test_allow_k3s_supervisor_crd if {
	req := _crd_request("CREATE", "addons.k3s.cattle.io", "system:k3s-supervisor")
	count({m | deny[m]; contains(m, "CRD operation")}) == 0 with input as {"request": req}
}

test_allow_apiserver_crd if {
	req := _crd_request("UPDATE", "something.cattle.io", "system:apiserver")
	count({m | deny[m]; contains(m, "CRD operation")}) == 0 with input as {"request": req}
}

# ---------------------------------------------------------------------------
# Bootstrap (admission-controller SA) exemption
# ---------------------------------------------------------------------------

test_allow_admission_controller_sa_crd if {
	req := _crd_request("CREATE", "anything.example.com", "system:serviceaccount:kube-system:admission-controller")
	count({m | deny[m]; contains(m, "CRD operation")}) == 0 with input as {"request": req}
}

# ---------------------------------------------------------------------------
# Gatekeeper CRD exemptions
# ---------------------------------------------------------------------------

test_allow_gatekeeper_crd_by_prefix if {
	req := _crd_request("CREATE", "gatekeeperconfigs.config.gatekeeper.sh", "miner")
	count({m | deny[m]; contains(m, "CRD operation")}) == 0 with input as {"request": req}
}

test_allow_gatekeeper_crd_by_suffix if {
	req := _crd_request("CREATE", "configs.config.gatekeeper.sh", "miner")
	count({m | deny[m]; contains(m, "CRD operation")}) == 0 with input as {"request": req}
}
