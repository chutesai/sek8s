# tests/unit/test_validators.py
"""
Unit tests for individual validators
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch
import aiohttp

from sek8s.config import CosignVerificationConfig
from sek8s.validators.base import ValidationResult
from sek8s.validators.cosign import CosignValidator, RateLimitError
from sek8s.cosign.client import CosignRateLimitError, CosignVerificationUnavailableError
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

    def test_resolve_to_full_ref_short_form(self):
        """Test resolve_to_full_ref for short form inputs."""
        from sek8s.system_manager.images.util import resolve_to_full_ref

        allowed = ["5fgap.localregistry.chutes.ai:30500", "localhost:30500"]
        assert (
            resolve_to_full_ref("sglang:nightly-123", allowed)
            == "5fgap.localregistry.chutes.ai:30500/chutes/sglang:nightly-123"
        )
        assert (
            resolve_to_full_ref("chutes/sglang:tag", allowed)
            == "5fgap.localregistry.chutes.ai:30500/chutes/sglang:tag"
        )
        # Full ref returned as-is
        full = "localhost:30500/chutes/sglang:tag"
        assert resolve_to_full_ref(full, allowed) == full

    def test_resolve_to_full_ref_prefers_validator_over_localhost(self):
        """When localhost is first, still prefer validator hostname so ref matches pods."""
        from sek8s.system_manager.images.util import resolve_to_full_ref

        # localhost first - we should still use validator so ref matches k8s deployments
        allowed = ["localhost:30500", "5fgap.localregistry.chutes.ai:30500"]
        assert (
            resolve_to_full_ref("sglang:tag", allowed)
            == "5fgap.localregistry.chutes.ai:30500/chutes/sglang:tag"
        )

    def test_resolve_to_full_ref_requires_validator_hostname(self):
        """Short form resolution fails when no validator hostname in allowed_registries."""
        from fastapi import HTTPException

        from sek8s.system_manager.images.util import resolve_to_full_ref

        # Only localhost - no validator hostname, must fail
        with pytest.raises(HTTPException) as exc:
            resolve_to_full_ref("sglang:tag", ["localhost:30500", "127.0.0.1:30500"])
        assert exc.value.status_code == 500
        assert ".localregistry.chutes.ai" in exc.value.detail

        # Empty list
        with pytest.raises(HTTPException) as exc:
            resolve_to_full_ref("sglang:tag", [])
        assert exc.value.status_code == 500

    def test_normalize_registry_hostname(self):
        """Test registry hostname lowercasing for ctr/registries.yaml match."""
        from sek8s.image_utils import normalize_registry_hostname

        assert (
            normalize_registry_hostname(
                "5FgapRUrM21n1HrHPa1uaGjywA3ayiZvG4RH2dvi3yHnt53M.localregistry.chutes.ai:30500/chutes/sglang:tag"
            )
            == "5fgaprurm21n1hrhpa1uagjywa3ayizvg4rh2dvi3yhnt53m.localregistry.chutes.ai:30500/chutes/sglang:tag"
        )
        assert normalize_registry_hostname("nginx:latest") == "nginx:latest"
        assert (
            normalize_registry_hostname("localhost:30500/org/image:tag")
            == "localhost:30500/org/image:tag"
        )

    def test_parse_image_reference_docker_hub_with_org(self):
        """Test parsing Docker Hub image with organization."""
        from sek8s.image_utils import parse_image_reference

        registry, org, repo, tag = parse_image_reference('parachutes/chutes-agent:k3s-latest')

        assert registry == 'docker.io'
        assert org == 'parachutes'
        assert repo == 'chutes-agent'
        assert tag == 'k3s-latest'

    def test_parse_image_reference_official_image(self):
        """Test parsing Docker Hub official image."""
        from sek8s.image_utils import parse_image_reference

        registry, org, repo, tag = parse_image_reference('nginx:latest')

        assert registry == 'docker.io'
        assert org == 'library'
        assert repo == 'nginx'
        assert tag == 'latest'

    def test_parse_image_reference_with_registry(self):
        """Test parsing image with explicit registry."""
        from sek8s.image_utils import parse_image_reference

        registry, org, repo, tag = parse_image_reference('gcr.io/google-containers/pause:3.9')

        assert registry == 'gcr.io'
        assert org == 'google-containers'
        assert repo == 'pause'
        assert tag == '3.9'

    def test_parse_image_reference_with_digest(self):
        """Test parsing image with digest."""
        from sek8s.image_utils import parse_image_reference

        registry, org, repo, tag = parse_image_reference(
            'docker.io/parachutes/app@sha256:abcd1234'
        )

        assert registry == 'docker.io'
        assert org == 'parachutes'
        assert repo == 'app'
        assert tag == '@sha256:abcd1234'

    def test_is_digest_pinned_reference(self):
        """Digest-pinned refs are detectable; tag-only refs are not."""
        from sek8s.image_utils import is_digest_pinned_reference

        assert is_digest_pinned_reference(
            "docker.io/library/nginx@sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        )
        assert is_digest_pinned_reference("gcr.io/p/foo/bar@sha512:abcd")
        assert not is_digest_pinned_reference("docker.io/library/nginx:latest")
        assert not is_digest_pinned_reference("nginx")

    @pytest.mark.asyncio
    async def test_digest_pinned_result_is_cached(self, config, tmp_path):
        """Digest-pinned image: first call verifies, second returns cached result."""
        key_file = tmp_path / "cosign.pub"
        key_file.write_text("test")
        vc = CosignVerificationConfig(
            verification_method="key",
            public_key=key_file,
            rekor_url="https://rekor.sigstore.dev",
        )
        validator = CosignValidator(config)
        calls = []

        async def count_verify(*args, **kwargs):
            calls.append(1)
            return True

        digest_img = "docker.io/test/img@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        with patch.object(validator._cosign_client, "verify", side_effect=count_verify):
            assert await validator._verify_image_signature(digest_img, vc) is True
            assert await validator._verify_image_signature(digest_img, vc) is True
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_tag_only_always_re_verifies(self, config, tmp_path):
        """Tag-only image refs can't be cached; cosign runs every time."""
        key_file = tmp_path / "cosign.pub"
        key_file.write_text("test")
        vc = CosignVerificationConfig(
            verification_method="key",
            public_key=key_file,
            rekor_url="https://rekor.sigstore.dev",
        )
        validator = CosignValidator(config)
        calls = []

        async def count_verify(*args, **kwargs):
            calls.append(1)
            return True

        with patch.object(validator._cosign_client, "verify", side_effect=count_verify):
            await validator._verify_image_signature("docker.io/test/img:latest", vc)
            await validator._verify_image_signature("docker.io/test/img:latest", vc)
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_failure_is_cached_for_digest(self, config, tmp_path):
        """Invalid signature cached so we don't re-verify the same bad image."""
        key_file = tmp_path / "cosign.pub"
        key_file.write_text("test")
        vc = CosignVerificationConfig(
            verification_method="key",
            public_key=key_file,
            rekor_url="https://rekor.sigstore.dev",
        )
        validator = CosignValidator(config)
        calls = []

        async def fail_verify(*args, **kwargs):
            calls.append(1)
            return False

        digest_img = "docker.io/test/img@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        with patch.object(validator._cosign_client, "verify", side_effect=fail_verify):
            assert await validator._verify_image_signature(digest_img, vc) is False
            assert await validator._verify_image_signature(digest_img, vc) is False
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_transient_error_cached_short_ttl(self, config, tmp_path):
        """Transient errors (network) are cached briefly so we don't spam the endpoint."""
        key_file = tmp_path / "cosign.pub"
        key_file.write_text("test")
        vc = CosignVerificationConfig(
            verification_method="key",
            public_key=key_file,
            rekor_url="https://rekor.sigstore.dev",
        )
        validator = CosignValidator(config)
        calls = []

        async def fail_transient(*args, **kwargs):
            calls.append(1)
            raise CosignVerificationUnavailableError("connection refused")

        digest_img = "docker.io/test/img@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
        with patch.object(validator._cosign_client, "verify", side_effect=fail_transient):
            with pytest.raises(CosignVerificationUnavailableError):
                await validator._verify_image_signature(digest_img, vc)
            with pytest.raises(CosignVerificationUnavailableError):
                await validator._verify_image_signature(digest_img, vc)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_rate_limit_sets_global_backoff(self, config, tmp_path):
        """Upstream 429 pauses all verifications, not just the triggering image."""
        key_file = tmp_path / "cosign.pub"
        key_file.write_text("test")
        vc = CosignVerificationConfig(
            verification_method="key",
            public_key=key_file,
            rekor_url="https://rekor.sigstore.dev",
        )
        validator = CosignValidator(config)

        async def raise_rl(*args, **kwargs):
            raise CosignRateLimitError("rate limited")

        with patch.object(validator._cosign_client, "verify", side_effect=raise_rl):
            with pytest.raises(CosignRateLimitError):
                await validator._verify_image_signature("docker.io/x:latest", vc)

        other_img = "docker.io/other@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
        with pytest.raises(RateLimitError):
            await validator._verify_image_signature(other_img, vc)

    @pytest.mark.asyncio
    async def test_concurrent_digest_verify_only_one_cosign_call(self, config, tmp_path):
        """Concurrent verify for same digest-pinned image: lock serialises, cache deduplicates."""
        key_file = tmp_path / "cosign.pub"
        key_file.write_text("test")
        vc = CosignVerificationConfig(
            verification_method="key",
            public_key=key_file,
            rekor_url="https://rekor.sigstore.dev",
        )
        validator = CosignValidator(config)
        calls = []

        async def slow_verify(*args, **kwargs):
            calls.append(1)
            await asyncio.sleep(0.02)
            return True

        digest_img = "docker.io/test/img@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        with patch.object(validator._cosign_client, "verify", side_effect=slow_verify):
            results = await asyncio.gather(
                *[validator._verify_image_signature(digest_img, vc) for _ in range(8)]
            )

        assert all(r is True for r in results)
        assert len(calls) == 1