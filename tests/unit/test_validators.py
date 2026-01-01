# tests/unit/test_validators.py
"""
Unit tests for individual validators
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch
import aiohttp

from sek8s.validators.base import ValidationResult
from sek8s.validators.cosign import CosignValidator
from sek8s.validators.registry import RegistryValidator
from sek8s.validators.opa import OPAValidator
from sek8s.config import AdmissionConfig, CosignConfig, NamespacePolicy


@pytest.fixture
def config():
    """Create test configuration."""
    return AdmissionConfig(
        opa_url="http://localhost:8181",
        opa_timeout=5.0,
        allowed_registries=["docker.io", "gcr.io", "quay.io", "localhost:30500"],
        enforcement_mode="enforce",
    )


class TestRegistryValidator:
    """Tests for RegistryValidator."""

    @pytest.mark.asyncio
    async def test_allowed_registry(self, config, valid_admission_review):
        """Test that allowed registries pass validation."""
        validator = RegistryValidator(config)
        result = await validator.validate(valid_admission_review)

        assert result.allowed is True
        assert len(result.messages) == 0

    @pytest.mark.asyncio
    async def test_disallowed_registry(self, config, untrusted_registry_review):
        """Test that disallowed registries are rejected."""
        validator = RegistryValidator(config)
        result = await validator.validate(untrusted_registry_review)

        assert result.allowed is False
        assert "disallowed registry" in result.messages[0]
        assert "untrusted-registry.com" in result.messages[0]

    @pytest.mark.asyncio
    async def test_docker_hub_short_form(self, config):
        """Test Docker Hub short form images (library/nginx)."""
        review = {
            "request": {
                "kind": {"kind": "Pod"},
                "namespace": "default",
                "object": {
                    "spec": {
                        "containers": [
                            {"image": "nginx:latest"}  # Docker Hub short form
                        ]
                    }
                },
            }
        }

        validator = RegistryValidator(config)
        result = await validator.validate(review)

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_localhost_registry(self, config):
        """Test localhost registry is allowed."""
        review = {
            "request": {
                "kind": {"kind": "Pod"},
                "namespace": "default",
                "object": {"spec": {"containers": [{"image": "localhost:30500/myapp:latest"}]}},
            }
        }

        validator = RegistryValidator(config)
        result = await validator.validate(review)

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_non_pod_resource_skipped(self, config, service_review):
        """Test that non-pod resources are skipped."""
        validator = RegistryValidator(config)
        result = await validator.validate(service_review)

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_deployment_template_validation(self, config, deployment_review):
        """Test that deployments are validated."""
        validator = RegistryValidator(config)
        result = await validator.validate(deployment_review)

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_exempt_namespace(self, config):
        """Test that exempt namespaces are handled correctly."""
        config.namespace_policies["test-exempt"] = NamespacePolicy(mode="warn", exempt=True)

        review = {
            "request": {
                "kind": {"kind": "Pod"},
                "namespace": "test-exempt",
                "object": {
                    "spec": {"containers": [{"image": "untrusted-registry.com/app:latest"}]}
                },
            }
        }

        validator = RegistryValidator(config)
        result = await validator.validate(review)

        assert result.allowed is True
        assert "exempt" in result.messages[0] if result.messages else True

    @pytest.mark.asyncio
    async def test_monitor_mode(self, config):
        """Test monitor mode allows but warns."""
        config.namespace_policies["default"].mode = "monitor"

        review = {
            "request": {
                "kind": {"kind": "Pod"},
                "namespace": "default",
                "object": {
                    "spec": {"containers": [{"image": "untrusted-registry.com/app:latest"}]}
                },
            }
        }

        validator = RegistryValidator(config)
        result = await validator.validate(review)

        assert result.allowed is True
        assert len(result.warnings) > 0
        assert "monitor mode" in result.warnings[0]


class TestOPAValidator:
    """Tests for OPAValidator."""

    @pytest.mark.asyncio
    async def test_opa_allow(self, config, valid_admission_review, mock_aiohttp_session):
        """Test OPA validator when OPA allows the request."""
        validator = OPAValidator(config)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"result": []})

        mock_session = mock_aiohttp_session(mock_response)

        validator.session = mock_session

        result = await validator.validate(valid_admission_review)

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_opa_deny_with_violations(
        self, config, privileged_pod_review, mock_aiohttp_session
    ):
        """Test OPA validator when OPA denies with violations."""
        validator = OPAValidator(config)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "result": [
                    {"msg": "Container 'app' has privileged security context"},
                    "Direct string violation",
                ]
            }
        )

        mock_session = mock_aiohttp_session(mock_response)

        validator.session = mock_session

        result = await validator.validate(privileged_pod_review)

        assert result.allowed is False
        assert "privileged security context" in result.messages[0]

    @pytest.mark.asyncio
    async def test_opa_timeout(self, config, valid_admission_review, mock_aiohttp_session):
        """Test OPA validator handles timeout (fail closed)."""
        validator = OPAValidator(config)

        mock_session = mock_aiohttp_session(None)
        mock_session.post.return_value.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())

        validator.session = mock_session

        result = await validator.validate(valid_admission_review)

        assert result.allowed is False
        assert "timeout" in result.messages[0].lower()

    @pytest.mark.asyncio
    async def test_opa_error_response(self, config, valid_admission_review, mock_aiohttp_session):
        """Test OPA validator handles error responses."""
        validator = OPAValidator(config)

        mock_response = AsyncMock()
        mock_response.status = 500
        mock_session = mock_aiohttp_session(mock_response)

        validator.session = mock_session

        result = await validator.validate(valid_admission_review)

        assert result.allowed is False
        assert "OPA returned status 500" in result.messages[0]

    @pytest.mark.asyncio
    async def test_opa_health_check_success(self, config, mock_aiohttp_session):
        """Test OPA health check when healthy."""
        validator = OPAValidator(config)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_session = mock_aiohttp_session(mock_response)

        validator.session = mock_session

        is_healthy = await validator.health_check()

        assert is_healthy is True

    @pytest.mark.asyncio
    async def test_opa_health_check_failure(self, config, mock_aiohttp_session):
        """Test OPA health check when unhealthy."""
        validator = OPAValidator(config)

        mock_session = mock_aiohttp_session(None)
        mock_session.get.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        validator.session = mock_session

        is_healthy = await validator.health_check()

        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_opa_warn_mode(self, config, privileged_pod_review, mock_aiohttp_session):
        """Test OPA validator in warn mode."""
        config.namespace_policies["default"].mode = "warn"
        validator = OPAValidator(config)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"result": [{"msg": "Policy violation"}]})
        mock_session = mock_aiohttp_session(mock_response)

        validator.session = mock_session

        result = await validator.validate(privileged_pod_review)

        assert result.allowed is True
        assert len(result.warnings) > 0
        assert "Policy violations detected" in result.warnings[0]


class TestValidationResult:
    """Tests for ValidationResult class."""

    def test_allow_result(self):
        """Test creating an allow result."""
        result = ValidationResult.allow("Success message", "Warning message")

        assert result.allowed is True
        assert "Success message" in result.messages
        assert "Warning message" in result.warnings

    def test_deny_result(self):
        """Test creating a deny result."""
        result = ValidationResult.deny("Denial reason")

        assert result.allowed is False
        assert "Denial reason" in result.messages
        assert len(result.warnings) == 0

    def test_combine_results_all_allowed(self):
        """Test combining multiple allowed results."""
        results = [
            ValidationResult.allow("Message 1", "Warning 1"),
            ValidationResult.allow("Message 2", "Warning 2"),
        ]

        combined = ValidationResult.combine(results)

        assert combined.allowed is True
        assert len(combined.messages) == 2
        assert len(combined.warnings) == 2

    def test_combine_results_with_denial(self):
        """Test combining results with at least one denial."""
        results = [
            ValidationResult.allow("Allowed"),
            ValidationResult.deny("Denied"),
            ValidationResult.allow(warning="Warning"),
        ]

        combined = ValidationResult.combine(results)

        assert combined.allowed is False
        assert "Denied" in combined.messages
        assert "Warning" in combined.warnings

class TestCosignValidator:
    """Tests for CosignValidator."""

    def test_parse_image_reference_docker_hub_with_org(self, config):
        """Test parsing Docker Hub image with organization."""
        validator = CosignValidator(config)

        registry, org, repo, tag = validator._parse_image_reference('parachutes/chutes-agent:k3s-latest')

        assert registry == 'docker.io'
        assert org == 'parachutes'
        assert repo == 'chutes-agent'
        assert tag == 'k3s-latest'

    def test_parse_image_reference_official_image(self, config):
        """Test parsing Docker Hub official image."""
        validator = CosignValidator(config)

        registry, org, repo, tag = validator._parse_image_reference('nginx:latest')

        assert registry == 'docker.io'
        assert org == 'library'
        assert repo == 'nginx'
        assert tag == 'latest'

    def test_parse_image_reference_with_registry(self, config):
        """Test parsing image with explicit registry."""
        validator = CosignValidator(config)

        registry, org, repo, tag = validator._parse_image_reference('gcr.io/google-containers/pause:3.9')

        assert registry == 'gcr.io'
        assert org == 'google-containers'
        assert repo == 'pause'
        assert tag == '3.9'

    def test_parse_image_reference_with_digest(self, config):
        """Test parsing image with digest."""
        validator = CosignValidator(config)

        registry, org, repo, tag = validator._parse_image_reference(
            'docker.io/parachutes/app@sha256:abcd1234'
        )

        assert registry == 'docker.io'
        assert org == 'parachutes'
        assert repo == 'app'
        assert tag == '@sha256:abcd1234'