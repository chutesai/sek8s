# tests/unit/test_webhook_server.py
"""
Unit tests for Admission Webhook Server
"""

import json
import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from unittest.mock import Mock, AsyncMock, patch

from sek8s.services.admission_controller import AdmissionWebhookServer
from sek8s.config import AdmissionConfig
from sek8s.validators.base import ValidationResult


class TestWebhookServer(AioHTTPTestCase):
    """Test webhook server endpoints."""

    async def get_application(self):
        """Create test application."""
        self.config = AdmissionConfig(bind_address="127.0.0.1", port=8443, debug=True)
        self.webhook_server = AdmissionWebhookServer(self.config)
        return self.webhook_server.app

    async def test_health_endpoint(self):
        """Test /health endpoint."""
        with patch.object(self.webhook_server.controller, "health_check") as mock_health:
            mock_health.return_value = {
                "healthy": True,
                "validators": {
                    "OPAValidator": {"healthy": True},
                    "RegistryValidator": {"healthy": True},
                },
            }

            resp = await self.client.request("GET", "/health")

            assert resp.status == 200
            data = await resp.json()
            assert data["healthy"] is True

    async def test_health_endpoint_unhealthy(self):
        """Test /health endpoint when unhealthy."""
        with patch.object(self.webhook_server.controller, "health_check") as mock_health:
            mock_health.return_value = {
                "healthy": False,
                "validators": {"OPAValidator": {"healthy": False, "error": "Connection failed"}},
            }

            resp = await self.client.request("GET", "/health")

            assert resp.status == 503
            data = await resp.json()
            assert data["healthy"] is False

    async def test_ready_endpoint(self):
        """Test /ready endpoint."""
        with patch.object(self.webhook_server.controller, "health_check") as mock_health:
            mock_health.return_value = {"healthy": True, "validators": {}}

            resp = await self.client.request("GET", "/ready")

            assert resp.status == 200
            data = await resp.json()
            assert data["ready"] is True

    async def test_ready_endpoint_not_ready(self):
        """Test /ready endpoint when not ready."""
        with patch.object(self.webhook_server.controller, "health_check") as mock_health:
            mock_health.return_value = {"healthy": False, "validators": {}}

            resp = await self.client.request("GET", "/ready")

            assert resp.status == 503
            data = await resp.json()
            assert data["ready"] is False

    async def test_metrics_endpoint(self):
        """Test /metrics endpoint."""
        resp = await self.client.request("GET", "/metrics")

        assert resp.status == 200
        text = await resp.text()
        assert "admission_controller_info" in text
        assert "admission_controller_uptime_seconds" in text

    async def test_validate_endpoint_success(self):
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

        with patch.object(self.webhook_server.controller, "validate_admission") as mock_validate:
            mock_validate.return_value = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {"uid": "test-123", "allowed": True},
            }

            resp = await self.client.post(
                "/validate",
                data=json.dumps(admission_review),
                headers={"Content-Type": "application/json"},
            )

            assert resp.status == 200
            data = await resp.json()
            assert data["response"]["allowed"] is True
            assert data["response"]["uid"] == "test-123"

    async def test_validate_endpoint_denial(self):
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

        with patch.object(self.webhook_server.controller, "validate_admission") as mock_validate:
            mock_validate.return_value = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": "test-456",
                    "allowed": False,
                    "status": {"message": "Pod violates security policy"},
                },
            }

            resp = await self.client.post(
                "/validate",
                data=json.dumps(admission_review),
                headers={"Content-Type": "application/json"},
            )

            assert resp.status == 200
            data = await resp.json()
            assert data["response"]["allowed"] is False
            assert "security policy" in data["response"]["status"]["message"]

    async def test_validate_endpoint_invalid_json(self):
        """Test /validate endpoint with invalid JSON."""
        resp = await self.client.post(
            "/validate", data="invalid json", headers={"Content-Type": "application/json"}
        )

        assert resp.status == 400
        data = await resp.json()
        assert "Invalid JSON" in data["error"]

    async def test_validate_endpoint_missing_request(self):
        """Test /validate endpoint with missing request field."""
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            # Missing "request"
        }

        resp = await self.client.post(
            "/validate",
            data=json.dumps(admission_review),
            headers={"Content-Type": "application/json"},
        )

        assert resp.status == 400
        data = await resp.json()
        assert "missing request" in data["error"]

    async def test_validate_endpoint_exception_handling(self):
        """Test /validate endpoint handles exceptions gracefully."""
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {"uid": "test-error", "operation": "CREATE", "object": {"kind": "Pod"}},
        }

        with patch.object(self.webhook_server.controller, "validate_admission") as mock_validate:
            mock_validate.side_effect = Exception("Unexpected error")

            resp = await self.client.post(
                "/validate",
                data=json.dumps(admission_review),
                headers={"Content-Type": "application/json"},
            )

            assert resp.status == 200  # Still returns 200 with deny response
            data = await resp.json()
            assert data["response"]["allowed"] is False
            assert "Internal server error" in data["response"]["status"]["message"]

    async def test_mutate_endpoint_placeholder(self):
        """Test /mutate endpoint (placeholder for future)."""
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {"uid": "test-mutate", "operation": "CREATE", "object": {"kind": "Pod"}},
        }

        resp = await self.client.post(
            "/mutate",
            data=json.dumps(admission_review),
            headers={"Content-Type": "application/json"},
        )

        assert resp.status == 200
        data = await resp.json()
        assert data["response"]["allowed"] is True
        assert data["response"]["uid"] == "test-mutate"
