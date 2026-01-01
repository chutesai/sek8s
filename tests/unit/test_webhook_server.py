# tests/unit/test_webhook_server.py
"""
Unit tests for Admission Webhook Server
"""

import json
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from sek8s.services.admission_controller import AdmissionWebhookServer
from sek8s.config import AdmissionConfig


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
    with patch.object(webhook_server.controller, "health_check", new_callable=AsyncMock) as mock_health:
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
    with patch.object(webhook_server.controller, "health_check", new_callable=AsyncMock) as mock_health:
        mock_health.return_value = {
            "healthy": False,
            "validators": {"OPAValidator": {"healthy": False, "error": "Connection failed"}},
        }

        resp = client.get("/health")

        assert resp.status_code == 503
        data = resp.json()
        assert data["healthy"] is False


def test_ready_endpoint(client, webhook_server):
    """Test /ready endpoint."""
    with patch.object(webhook_server.controller, "health_check", new_callable=AsyncMock) as mock_health:
        mock_health.return_value = {"healthy": True, "validators": {}}

        resp = client.get("/ready")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True


def test_ready_endpoint_not_ready(client, webhook_server):
    """Test /ready endpoint when not ready."""
    with patch.object(webhook_server.controller, "health_check", new_callable=AsyncMock) as mock_health:
        mock_health.return_value = {"healthy": False, "validators": {}}

        resp = client.get("/ready")

        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False


def test_metrics_endpoint(client):
    """Test /metrics endpoint."""
    resp = client.get("/metrics")

    assert resp.status_code == 200
    text = resp.text
    assert "admission_controller_info" in text
    assert "admission_controller_uptime_seconds" in text


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

    with patch.object(webhook_server.controller, "validate_admission", new_callable=AsyncMock) as mock_validate:
        mock_validate.return_value = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {"uid": "test-123", "allowed": True},
        }

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

    with patch.object(webhook_server.controller, "validate_admission", new_callable=AsyncMock) as mock_validate:
        mock_validate.return_value = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {
                "uid": "test-456",
                "allowed": False,
                "status": {"message": "Pod violates security policy"},
            },
        }

        resp = client.post("/validate", json=admission_review)

        assert resp.status_code == 200
        data = resp.json()
        assert data["response"]["allowed"] is False
        assert "security policy" in data["response"]["status"]["message"]


def test_validate_endpoint_invalid_json(client):
    """Test /validate endpoint with invalid JSON."""
    resp = client.post("/validate", data="invalid json", headers={"Content-Type": "application/json"})

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
        "request": {"uid": "test-error", "operation": "CREATE", "object": {"kind": "Pod"}},
    }

    with patch.object(webhook_server.controller, "validate_admission", new_callable=AsyncMock) as mock_validate:
        mock_validate.side_effect = Exception("Unexpected error")

        resp = client.post("/validate", json=admission_review)

        assert resp.status_code == 200  # Still returns 200 with deny response
        data = resp.json()
        assert data["apiVersion"] == "admission.k8s.io/v1"
        assert data["kind"] == "AdmissionReview"
        assert data["response"]["allowed"] is False
        assert data["response"]["uid"] == "test-error"
        assert "Internal server error" in data["response"]["status"]["message"]


def test_mutate_endpoint_placeholder(client):
    """Test /mutate endpoint (placeholder for future)."""
    admission_review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {"uid": "test-mutate", "operation": "CREATE", "object": {"kind": "Pod"}},
    }

    resp = client.post("/mutate", json=admission_review)

    assert resp.status_code == 200
    data = resp.json()
    assert data["response"]["allowed"] is True
    assert data["response"]["uid"] == "test-mutate"
