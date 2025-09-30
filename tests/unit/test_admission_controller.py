# tests/unit/test_admission_controller.py
"""
Unit tests for async Admission Controller
"""

import asyncio
import json
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from sek8s.services.admission_controller import AdmissionController, AdmissionWebhookServer
from sek8s.validators.base import ValidationResult
from sek8s.metrics import MetricsCollector


@pytest.mark.asyncio
async def test_validate_allowed_pod(admission_controller, valid_admission_review):
    """Test validation of an allowed pod."""
    # Mock validators to return allow
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(return_value=ValidationResult.allow())
        mock_validators.__iter__ = Mock(return_value=iter([mock_validator]))
        
        response = await admission_controller.validate_admission(valid_admission_review)
        
        assert response["response"]["allowed"] is True
        assert response["response"]["uid"] == "test-uid-123"


@pytest.mark.asyncio
async def test_validate_denied_pod_privileged(admission_controller, privileged_pod_review):
    """Test rejection of privileged pod by OPA."""
    # Mock OPA validator to deny
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_opa = AsyncMock()
        mock_opa.validate = AsyncMock(
            return_value=ValidationResult.deny("Container 'app' has privileged security context")
        )
        mock_registry = AsyncMock()
        mock_registry.validate = AsyncMock(return_value=ValidationResult.allow())
        mock_validators.__iter__ = Mock(return_value=iter([mock_opa, mock_registry]))
        
        response = await admission_controller.validate_admission(privileged_pod_review)
        
        assert response["response"]["allowed"] is False
        assert "privileged security context" in response["response"]["status"]["message"]


@pytest.mark.asyncio
async def test_validate_untrusted_registry(admission_controller, untrusted_registry_review):
    """Test rejection of pod from untrusted registry."""
    # Mock registry validator to deny
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_opa = AsyncMock()
        mock_opa.validate = AsyncMock(return_value=ValidationResult.allow())
        mock_registry = AsyncMock()
        mock_registry.validate = AsyncMock(
            return_value=ValidationResult.deny("Image untrusted-registry.com/malicious:latest uses disallowed registry")
        )
        mock_validators.__iter__ = Mock(return_value=iter([mock_opa, mock_registry]))
        
        response = await admission_controller.validate_admission(untrusted_registry_review)
        
        assert response["response"]["allowed"] is False
        assert "disallowed registry" in response["response"]["status"]["message"]


@pytest.mark.asyncio
async def test_validate_with_warnings(admission_controller, valid_admission_review):
    """Test validation with warnings (warn mode)."""
    # Mock validator to return warning
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(
            return_value=ValidationResult.allow(warning="Policy violation detected (monitor mode)")
        )
        mock_validators.__iter__ = Mock(return_value=iter([mock_validator]))
        
        response = await admission_controller.validate_admission(valid_admission_review)
        
        assert response["response"]["allowed"] is True
        assert "warnings" in response["response"]
        assert "Policy violation detected" in response["response"]["warnings"][0]


@pytest.mark.asyncio
async def test_cache_hit(admission_controller, valid_admission_review):
    """Test cache hit for repeated requests."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(return_value=ValidationResult.allow())
        mock_validators.__iter__ = Mock(return_value=iter([mock_validator]))
        
        # First request
        response1 = await admission_controller.validate_admission(valid_admission_review)
        assert response1["response"]["allowed"] is True
        
        # Second request (should hit cache)
        response2 = await admission_controller.validate_admission(valid_admission_review)
        assert response2["response"]["allowed"] is True
        
        # Validator should only be called once due to caching
        mock_validator.validate.assert_called_once()


@pytest.mark.asyncio
async def test_validator_exception_handling(admission_controller, valid_admission_review):
    """Test handling of validator exceptions (fail closed)."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(side_effect=Exception("Validator error"))
        mock_validators.__iter__ = Mock(return_value=iter([mock_validator]))
        
        response = await admission_controller.validate_admission(valid_admission_review)
        
        assert response["response"]["allowed"] is False
        assert "Internal error" in response["response"]["status"]["message"]


@pytest.mark.asyncio
async def test_deployment_validation(admission_controller, deployment_review):
    """Test validation of deployment resources."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(return_value=ValidationResult.allow())
        mock_validators.__iter__ = Mock(return_value=iter([mock_validator]))
        
        response = await admission_controller.validate_admission(deployment_review)
        
        assert response["response"]["allowed"] is True


@pytest.mark.asyncio
async def test_cronjob_validation(admission_controller, cronjob_review):
    """Test validation of CronJob resources."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(return_value=ValidationResult.allow())
        mock_validators.__iter__ = Mock(return_value=iter([mock_validator]))
        
        response = await admission_controller.validate_admission(cronjob_review)
        
        assert response["response"]["allowed"] is True


@pytest.mark.asyncio
async def test_namespace_creation_denied(admission_controller, namespace_creation_review):
    """Test that namespace creation is denied."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_opa = AsyncMock()
        mock_opa.validate = AsyncMock(
            return_value=ValidationResult.deny("Creation of new namespaces is prohibited")
        )
        mock_validators.__iter__ = Mock(return_value=iter([mock_opa]))
        
        response = await admission_controller.validate_admission(namespace_creation_review)
        
        assert response["response"]["allowed"] is False
        assert "namespaces is prohibited" in response["response"]["status"]["message"]


@pytest.mark.asyncio
async def test_host_network_denied(admission_controller, host_network_pod_review):
    """Test rejection of pod with host network."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_opa = AsyncMock()
        mock_opa.validate = AsyncMock(
            return_value=ValidationResult.deny("Pod uses host network which is not allowed")
        )
        mock_validators.__iter__ = Mock(return_value=iter([mock_opa]))
        
        response = await admission_controller.validate_admission(host_network_pod_review)
        
        assert response["response"]["allowed"] is False
        assert "host network" in response["response"]["status"]["message"]


@pytest.mark.asyncio
async def test_invalid_hostpath_denied(admission_controller, invalid_hostpath_review):
    """Test rejection of invalid hostPath volumes."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_opa = AsyncMock()
        mock_opa.validate = AsyncMock(
            return_value=ValidationResult.deny("hostPath volume '/etc' not allowed. Only /cache paths are permitted")
        )
        mock_validators.__iter__ = Mock(return_value=iter([mock_opa]))
        
        response = await admission_controller.validate_admission(invalid_hostpath_review)
        
        assert response["response"]["allowed"] is False
        assert "/cache paths are permitted" in response["response"]["status"]["message"]


@pytest.mark.asyncio
async def test_valid_cache_mount_allowed(admission_controller, valid_cache_mount_review):
    """Test that valid /cache mounts are allowed."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(return_value=ValidationResult.allow())
        mock_validators.__iter__ = Mock(return_value=iter([mock_validator]))
        
        response = await admission_controller.validate_admission(valid_cache_mount_review)
        
        assert response["response"]["allowed"] is True


@pytest.mark.asyncio
async def test_no_resource_limits_denied(admission_controller, no_limits_pod_review):
    """Test rejection of pods without resource limits."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_opa = AsyncMock()
        mock_opa.validate = AsyncMock(
            return_value=ValidationResult.deny("Container 'app' missing resource limits")
        )
        mock_validators.__iter__ = Mock(return_value=iter([mock_opa]))
        
        response = await admission_controller.validate_admission(no_limits_pod_review)
        
        assert response["response"]["allowed"] is False
        assert "missing resource limits" in response["response"]["status"]["message"]


@pytest.mark.asyncio
async def test_service_allowed(admission_controller, service_review):
    """Test that service resources are allowed."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(return_value=ValidationResult.allow())
        mock_validators.__iter__ = Mock(return_value=iter([mock_validator]))
        
        response = await admission_controller.validate_admission(service_review)
        
        assert response["response"]["allowed"] is True


@pytest.mark.asyncio
async def test_exempt_namespace(admission_controller, exempt_namespace_review):
    """Test that exempt namespaces bypass certain validations."""
    # In exempt namespace, registry validator should still check but with monitor mode
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(
            return_value=ValidationResult.allow(
                warning="Registry violations (monitor mode): Image untrusted-registry.com/app:latest uses disallowed registry"
            )
        )
        mock_validators.__iter__ = Mock(return_value=iter([mock_validator]))
        
        response = await admission_controller.validate_admission(exempt_namespace_review)
        
        assert response["response"]["allowed"] is True
        assert "warnings" in response["response"]


@pytest.mark.asyncio
async def test_multiple_validators_combined(admission_controller, valid_admission_review):
    """Test multiple validators results are combined correctly."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_opa = AsyncMock()
        mock_opa.validate = AsyncMock(
            return_value=ValidationResult.allow(warning="OPA warning")
        )
        mock_registry = AsyncMock()
        mock_registry.validate = AsyncMock(
            return_value=ValidationResult.allow(warning="Registry warning")
        )
        mock_validators.__iter__ = Mock(return_value=iter([mock_opa, mock_registry]))
        
        response = await admission_controller.validate_admission(valid_admission_review)
        
        assert response["response"]["allowed"] is True
        assert len(response["response"]["warnings"]) == 2


@pytest.mark.asyncio
async def test_health_check(admission_controller):
    """Test health check aggregates validator health."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_healthy = AsyncMock()
        mock_healthy.health_check = AsyncMock(return_value=True)
        mock_healthy.__class__.__name__ = "HealthyValidator"
        
        mock_unhealthy = AsyncMock()
        mock_unhealthy.health_check = AsyncMock(return_value=False)
        mock_unhealthy.__class__.__name__ = "UnhealthyValidator"
        
        mock_validators.__iter__ = Mock(return_value=iter([mock_healthy, mock_unhealthy]))
        
        health_status = await admission_controller.health_check()
        
        assert health_status["healthy"] is False
        assert health_status["validators"]["HealthyValidator"]["healthy"] is True
        assert health_status["validators"]["UnhealthyValidator"]["healthy"] is False


@pytest.mark.asyncio
async def test_metrics_recording(admission_controller, valid_admission_review):
    """Test that metrics are recorded correctly."""
    with patch.object(admission_controller, 'validators') as mock_validators:
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(return_value=ValidationResult.allow())
        mock_validators.__iter__ = Mock(return_value=iter([mock_validator]))
        
        # Reset metrics
        admission_controller.metrics = MetricsCollector()
        
        response = await admission_controller.validate_admission(valid_admission_review)
        
        assert admission_controller.metrics.admission_total["allowed"] == 1
        assert "Pod_allowed" in admission_controller.metrics.admission_by_kind
        assert "CREATE_allowed" in admission_controller.metrics.admission_by_operation