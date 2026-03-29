# tests/unit/test_webhook_server.py
"""
Unit tests for Admission Webhook Server
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sek8s.config import AdmissionConfig
from sek8s.services.admission_controller import AdmissionWebhookServer
from sek8s.services.admission_models import (
    AdmissionResponseBody,
    AdmissionReviewResponse,
    AdmissionStatus,
)


@pytest.fixture
def webhook_server():
    """Create webhook server instance."""
    config = AdmissionConfig(bind_address="127.0.0.1", port=8443, debug=True)
    return AdmissionWebhookServer(config)


@pytest.fixture
def client(webhook_server):
    """Create test client."""
    return TestClient(webhook_server.app)


def test_health_endpoint(client, webhook_server):
    """Test /health endpoint."""
    with patch.object(
        webhook_server.controller, "health_check", new_callable=AsyncMock
    ) as mock_health:
        mock_health.return_value = {
            "healthy": True,
            "validators": {
                "OPAValidator": {"healthy": True},
                "RegistryValidator": {"healthy": True},
            },
        }

        resp = client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is True


def test_health_endpoint_unhealthy(client, webhook_server):
    """Test /health endpoint when unhealthy."""
    with patch.object(
        webhook_server.controller, "health_check", new_callable=AsyncMock
    ) as mock_health:
        mock_health.return_value = {
            "healthy": False,
            "validators": {
                "OPAValidator": {"healthy": False, "error": "Connection failed"}
            },
        }

        resp = client.get("/health")

        assert resp.status_code == 503
        data = resp.json()
        assert data["healthy"] is False


def test_ready_endpoint(client, webhook_server):
    """Test /ready endpoint."""
    with patch.object(
        webhook_server.controller, "health_check", new_callable=AsyncMock
    ) as mock_health:
        mock_health.return_value = {"healthy": True, "validators": {}}

        resp = client.get("/ready")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True


def test_ready_endpoint_not_ready(client, webhook_server):
    """Test /ready endpoint when not ready."""
    with patch.object(
        webhook_server.controller, "health_check", new_callable=AsyncMock
    ) as mock_health:
        mock_health.return_value = {"healthy": False, "validators": {}}

        resp = client.get("/ready")

        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False


def test_validate_endpoint_success(client, webhook_server):
    """Test /validate endpoint with successful validation."""
    admission_review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-123",
            "operation": "CREATE",
            "object": {"kind": "Pod", "metadata": {"name": "test"}},
        },
    }

    with patch.object(
        webhook_server.controller, "validate_admission", new_callable=AsyncMock
    ) as mock_validate:
        mock_validate.return_value = AdmissionReviewResponse(
            response=AdmissionResponseBody(uid="test-123", allowed=True),
        )

        resp = client.post("/validate", json=admission_review)

        assert resp.status_code == 200
        data = resp.json()
        assert data["response"]["allowed"] is True
        assert data["response"]["uid"] == "test-123"


def test_validate_endpoint_denial(client, webhook_server):
    """Test /validate endpoint with denied validation."""
    admission_review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-456",
            "operation": "CREATE",
            "object": {"kind": "Pod", "metadata": {"name": "bad-pod"}},
        },
    }

    with patch.object(
        webhook_server.controller, "validate_admission", new_callable=AsyncMock
    ) as mock_validate:
        mock_validate.return_value = AdmissionReviewResponse(
            response=AdmissionResponseBody(
                uid="test-456",
                allowed=False,
                status=AdmissionStatus(message="Pod violates security policy"),
            ),
        )

        resp = client.post("/validate", json=admission_review)

        assert resp.status_code == 200
        data = resp.json()
        assert data["response"]["allowed"] is False
        assert "security policy" in data["response"]["status"]["message"]


def test_validate_endpoint_invalid_json(client):
    """Test /validate endpoint with invalid JSON."""
    resp = client.post(
        "/validate", data="invalid json", headers={"Content-Type": "application/json"}
    )

    assert resp.status_code == 400
    data = resp.json()
    assert "Invalid JSON" in data["error"]


def test_validate_endpoint_missing_request(client):
    """Test /validate endpoint with missing request field."""
    admission_review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        # Missing "request"
    }

    resp = client.post("/validate", json=admission_review)

    assert resp.status_code == 400
    data = resp.json()
    assert "missing request" in data["error"]


def test_validate_endpoint_exception_handling(client, webhook_server):
    """Test /validate endpoint handles exceptions gracefully."""
    admission_review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-error",
            "operation": "CREATE",
            "object": {"kind": "Pod"},
        },
    }

    with patch.object(
        webhook_server.controller, "validate_admission", new_callable=AsyncMock
    ) as mock_validate:
        mock_validate.side_effect = Exception("Unexpected error")

        resp = client.post("/validate", json=admission_review)

        assert resp.status_code == 200  # Still returns 200 with deny response
        data = resp.json()
        assert data["apiVersion"] == "admission.k8s.io/v1"
        assert data["kind"] == "AdmissionReview"
        assert data["response"]["allowed"] is False
        assert data["response"]["uid"] == "test-error"
        assert "Internal server error" in data["response"]["status"]["message"]


def _decode_mutate_patch(resp_json):
    import base64

    patch_b64 = resp_json["response"].get("patch")
    if patch_b64 is None:
        return []
    return json.loads(base64.b64decode(patch_b64))


def test_mutate_pod_in_chutes_adds_patch(client):
    """Pod in chutes namespace without automountServiceAccountToken gets patched."""
    review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "sa-pod-1",
            "operation": "CREATE",
            "namespace": "chutes",
            "kind": {"kind": "Pod"},
            "object": {"spec": {"containers": [{"name": "app", "image": "busybox"}]}},
        },
    }
    resp = client.post("/mutate", json=review)
    assert resp.status_code == 200
    data = resp.json()
    assert data["response"]["allowed"] is True
    patches = _decode_mutate_patch(data)
    assert any(
        p["path"] == "/spec/automountServiceAccountToken" and p["value"] is False
        for p in patches
    )


def test_mutate_pod_already_false_no_patch(client):
    """Pod with automountServiceAccountToken: false gets no patch."""
    review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "sa-pod-2",
            "operation": "CREATE",
            "namespace": "chutes",
            "kind": {"kind": "Pod"},
            "object": {
                "spec": {
                    "automountServiceAccountToken": False,
                    "containers": [{"name": "app", "image": "busybox"}],
                }
            },
        },
    }
    resp = client.post("/mutate", json=review)
    assert resp.status_code == 200
    data = resp.json()
    assert data["response"]["allowed"] is True
    assert data["response"].get("patch") is None


def test_mutate_job_in_chutes_adds_patch(client):
    """Job in chutes namespace gets template spec patched."""
    review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "sa-job-1",
            "operation": "CREATE",
            "namespace": "chutes",
            "kind": {"kind": "Job"},
            "object": {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [{"name": "app", "image": "busybox"}],
                        }
                    }
                }
            },
        },
    }
    resp = client.post("/mutate", json=review)
    assert resp.status_code == 200
    patches = _decode_mutate_patch(resp.json())
    assert any(
        p["path"] == "/spec/template/spec/automountServiceAccountToken"
        and p["value"] is False
        for p in patches
    )


def test_mutate_deployment_in_chutes_adds_patch(client):
    """Deployment in chutes namespace gets template spec patched."""
    review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "sa-deploy-1",
            "operation": "CREATE",
            "namespace": "chutes",
            "kind": {"kind": "Deployment"},
            "object": {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [{"name": "app", "image": "busybox"}],
                        }
                    }
                }
            },
        },
    }
    resp = client.post("/mutate", json=review)
    assert resp.status_code == 200
    patches = _decode_mutate_patch(resp.json())
    assert any(
        p["path"] == "/spec/template/spec/automountServiceAccountToken"
        and p["value"] is False
        for p in patches
    )


def test_mutate_cronjob_in_chutes_adds_patch(client):
    """CronJob in chutes namespace gets nested spec patched."""
    review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "sa-cron-1",
            "operation": "CREATE",
            "namespace": "chutes",
            "kind": {"kind": "CronJob"},
            "object": {
                "spec": {
                    "jobTemplate": {
                        "spec": {
                            "template": {
                                "spec": {
                                    "containers": [{"name": "app", "image": "busybox"}],
                                }
                            }
                        }
                    }
                }
            },
        },
    }
    resp = client.post("/mutate", json=review)
    assert resp.status_code == 200
    patches = _decode_mutate_patch(resp.json())
    assert any(
        p["path"] == "/spec/jobTemplate/spec/template/spec/automountServiceAccountToken"
        and p["value"] is False
        for p in patches
    )


def test_mutate_non_chutes_namespace_no_patch(client):
    """Pod in a non-chutes namespace gets no patch."""
    review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "sa-sys-1",
            "operation": "CREATE",
            "namespace": "kube-system",
            "kind": {"kind": "Pod"},
            "object": {
                "spec": {"containers": [{"name": "coredns", "image": "coredns"}]}
            },
        },
    }
    resp = client.post("/mutate", json=review)
    assert resp.status_code == 200
    data = resp.json()
    assert data["response"]["allowed"] is True
    assert data["response"].get("patch") is None


def test_mutate_invalid_json(client):
    """SEK8S-047: Invalid JSON on /mutate returns 400 (not allowed=true)."""
    resp = client.post(
        "/mutate", data="not json", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 400
