# OPA tests for SEK8S-024: Service restrictions in chutes namespace.
# Chute services must be NodePort type with chutes/chute label.
# ExternalName services are blocked (traffic redirection attack).
# Run: ./bin/opa test ansible/k3s/roles/admission-controller/files/policies tests/opa -v
package kubernetes.admission

import future.keywords.if
import future.keywords.in

# =============================================================================
# SEK8S-024: Block ExternalName services in chutes namespace
# ExternalName services can redirect traffic to arbitrary internal services.
# =============================================================================

test_deny_externalname_service_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Service"},
		"object": {
			"metadata": {
				"name": "redirect-to-internal",
				"labels": {"chutes/chute": "true"},
			},
			"spec": {
				"type": "ExternalName",
				"externalName": "attestation-service-internal.attestation-system.svc.cluster.local",
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "ExternalName")}) > 0 with input as {"request": req}
}

# =============================================================================
# SEK8S-024: Block services without chutes/chute label in chutes namespace
# =============================================================================

test_deny_service_without_chute_label_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Service"},
		"object": {
			"metadata": {
				"name": "rogue-service",
				"labels": {},
			},
			"spec": {
				"type": "NodePort",
				"ports": [{"port": 8000, "targetPort": 8000, "protocol": "TCP"}],
				"selector": {"app": "evil"},
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "chutes/chute")}) > 0 with input as {"request": req}
}

test_deny_service_without_labels_in_chutes if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Service"},
		"object": {
			"metadata": {
				"name": "rogue-service",
			},
			"spec": {
				"type": "NodePort",
				"ports": [{"port": 8000, "targetPort": 8000, "protocol": "TCP"}],
				"selector": {"app": "evil"},
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "chutes/chute")}) > 0 with input as {"request": req}
}

# =============================================================================
# SEK8S-024: Allow legitimate chute services
# =============================================================================

test_allow_chute_nodeport_service if {
	req := {
		"operation": "CREATE",
		"namespace": "chutes",
		"kind": {"kind": "Service"},
		"object": {
			"metadata": {
				"name": "chute-svc-abc123",
				"labels": {
					"chutes/deployment-id": "abc123",
					"chutes/chute": "true",
					"chutes/chute-id": "test-chute",
					"chutes/version": "1.0",
				},
			},
			"spec": {
				"type": "NodePort",
				"externalTrafficPolicy": "Local",
				"selector": {"chutes/deployment-id": "abc123"},
				"ports": [
					{"port": 8000, "targetPort": 8000, "protocol": "TCP", "name": "chute-8000"},
					{"port": 8001, "targetPort": 8001, "protocol": "TCP", "name": "chute-8001"},
				],
			},
		},
		"userInfo": {"username": "miner"},
	}
	count({m | deny[m]; contains(m, "ExternalName")}) == 0 with input as {"request": req}
	count({m | deny[m]; contains(m, "chutes/chute")}) == 0 with input as {"request": req}
}

# =============================================================================
# SEK8S-024: System namespaces should not be affected
# =============================================================================

test_allow_service_in_system_namespace if {
	req := {
		"operation": "CREATE",
		"namespace": "attestation-system",
		"kind": {"kind": "Service"},
		"object": {
			"metadata": {
				"name": "attestation-service-internal",
				"labels": {"app": "attestation-service"},
			},
			"spec": {
				"type": "ClusterIP",
				"ports": [{"port": 8443, "targetPort": 8444, "protocol": "TCP"}],
			},
		},
		"userInfo": {"username": "system:serviceaccount:attestation-system:attestation-proxy"},
	}
	count({m | deny[m]; contains(m, "ExternalName")}) == 0 with input as {"request": req}
	count({m | deny[m]; contains(m, "chutes/chute")}) == 0 with input as {"request": req}
}
