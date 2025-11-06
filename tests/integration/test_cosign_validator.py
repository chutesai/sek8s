"""
Integration tests for CosignValidator with real local registry and signed images.
Now includes tests for registry-specific configuration.

Prerequisites:
- Registry running at localhost:5000 with test images
- OPA running at localhost:8181
- Test images already built and signed
- Cosign configuration JSON file with registry-specific settings
"""

import json
import subprocess
import pytest
import tempfile
from pathlib import Path

from sek8s.config import AdmissionConfig, CosignConfig
from sek8s.validators.cosign import CosignValidator
from sek8s.validators.base import ValidationResult


# Test configuration
REGISTRY_URL = "localhost:5000"
COSIGN_KEY_PATH = Path("tests/integration/keys/cosign.pub")
WRONG_KEY_PATH = Path("tests/integration/keys/wrong.pub")
NONEXISTENT_KEY_PATH = Path("tests/integration/keys/nonexistent.pub")


def check_prerequisites():
    """Check that required services and test images are available."""
    # Check registry
    try:
        result = subprocess.run(
            ["curl", "-f", f"http://{REGISTRY_URL}/v2/"], check=True, capture_output=True
        )
    except subprocess.CalledProcessError:
        pytest.skip(
            f"Registry not available at {REGISTRY_URL}. Run 'make integration-setup' first."
        )

    # Check OPA
    try:
        result = subprocess.run(
            ["curl", "-f", "http://localhost:8181/health"], check=True, capture_output=True
        )
    except subprocess.CalledProcessError:
        pytest.skip("OPA not available at localhost:8181. Run 'make integration-setup' first.")

    # Check test images exist
    try:
        result = subprocess.run(
            ["curl", "-s", f"http://{REGISTRY_URL}/v2/test-app/tags/list"],
            check=True,
            capture_output=True,
            text=True,
        )

        tags_info = json.loads(result.stdout)
        tags = tags_info.get("tags", [])

        expected_tags = ["signed", "unsigned", "wrongsig"]
        missing_tags = [tag for tag in expected_tags if tag not in tags]

        if missing_tags:
            pytest.skip(f"Missing test images: {missing_tags}. Run 'make integration-setup' first.")

    except (subprocess.CalledProcessError, json.JSONDecodeError):
        pytest.skip("Test images not available. Run 'make integration-setup' first.")

    # Check cosign keys
    if not COSIGN_KEY_PATH.exists():
        pytest.skip(
            f"Cosign public key not found at {COSIGN_KEY_PATH}. Run 'make integration-setup' first."
        )


@pytest.fixture(scope="session", autouse=True)
def verify_test_environment():
    """Verify test environment is properly set up."""
    check_prerequisites()
    print("✓ Integration test prerequisites verified")


def create_cosign_config_file(config_data: dict) -> Path:
    """Create a temporary cosign configuration file."""
    temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(config_data, temp_file, indent=2)
    temp_file.flush()
    return Path(temp_file.name)


@pytest.fixture
def config():
    """Create test configuration."""
    return AdmissionConfig(
        allowed_registries=[REGISTRY_URL, "docker.io"], enforcement_mode="enforce"
    )


@pytest.fixture
def default_cosign_config():
    """Create default cosign configuration with key-based verification."""
    config_data = {
        "registries": [
            {
                "registry": REGISTRY_URL,
                "require_signature": True,
                "verification_method": "key",
                "public_key": str(COSIGN_KEY_PATH),
            },
            {
                "registry": "*",
                "require_signature": True,
                "verification_method": "key",
                "public_key": str(COSIGN_KEY_PATH),
            },
        ]
    }
    config_file = create_cosign_config_file(config_data)
    return CosignConfig(cosign_registries_file=config_file)


@pytest.fixture
def disabled_cosign_config():
    """Create cosign configuration with verification disabled."""
    config_data = {
        "registries": [
            {
                "registry": REGISTRY_URL,
                "require_signature": False,
                "verification_method": "disabled",
            },
            {
                "registry": "*",
                "require_signature": True,
                "verification_method": "key",
                "public_key": str(COSIGN_KEY_PATH),
            },
        ]
    }
    config_file = create_cosign_config_file(config_data)
    return CosignConfig(cosign_registries_file=config_file)


@pytest.fixture
def mixed_registry_cosign_config():
    """Create cosign configuration with different settings per registry."""
    config_data = {
        "registries": [
            {
                "registry": REGISTRY_URL,
                "require_signature": True,
                "verification_method": "key",
                "public_key": str(COSIGN_KEY_PATH),
            },
            {
                "registry": "docker.io",
                "require_signature": False,
                "verification_method": "disabled",
            },
            {
                "registry": "gcr.io",
                "require_signature": True,
                "verification_method": "keyless",
                "keyless_identity_regex": "^https://github.com/.*",
                "keyless_issuer": "https://token.actions.githubusercontent.com",
            },
            {
                "registry": "*",
                "require_signature": True,
                "verification_method": "key",
                "public_key": str(COSIGN_KEY_PATH),
            },
        ]
    }
    config_file = create_cosign_config_file(config_data)
    return CosignConfig(cosign_registries_file=config_file)


def create_cosign_validator_with_config(
    config: AdmissionConfig, cosign_config: CosignConfig
) -> CosignValidator:
    """Create a CosignValidator with custom cosign configuration."""
    validator = CosignValidator(config)
    validator.cosign_config = cosign_config
    return validator


def create_admission_review(image_name: str, namespace: str = "default") -> dict:
    """Create an admission review for testing."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": f"test-{image_name.replace(':', '-').replace('/', '-')}",
            "operation": "CREATE",
            "namespace": namespace,
            "name": "test-pod",
            "kind": {"kind": "Pod", "version": "v1", "group": ""},
            "object": {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "test-pod", "namespace": namespace},
                "spec": {
                    "containers": [
                        {
                            "name": "test-container",
                            "image": image_name,
                            "resources": {"limits": {"memory": "256Mi", "cpu": "500m"}},
                        }
                    ]
                },
            },
        },
    }


# Registry extraction tests
def test_registry_extraction():
    """Test registry extraction from various image formats."""
    validator = CosignValidator(AdmissionConfig())

    test_cases = [
        ("nginx", "docker.io"),
        ("nginx:latest", "docker.io"),
        ("library/nginx", "docker.io"),
        ("myuser/myrepo", "docker.io"),
        ("gcr.io/project/image", "gcr.io"),
        ("gcr.io/project/image:tag", "gcr.io"),
        ("localhost:5000/myimage", "localhost:5000"),
        ("localhost:5000/myimage:latest", "localhost:5000"),
        ("registry.example.com:443/app", "registry.example.com:443"),
        ("quay.io/user/app@sha256:abc123", "quay.io"),
        ("localhost:5000/app@sha256:def456", "localhost:5000"),
    ]

    for image, expected_registry in test_cases:
        actual_registry = validator._extract_registry(image)
        assert actual_registry == expected_registry, (
            f"Expected {expected_registry} for image {image}, got {actual_registry}"
        )


# Configuration-based tests
@pytest.mark.asyncio
async def test_signed_image_with_key_verification(config, default_cosign_config):
    """Test that properly signed images are allowed with key verification."""
    validator = create_cosign_validator_with_config(config, default_cosign_config)
    image_name = f"{REGISTRY_URL}/test-app:signed"
    admission_review = create_admission_review(image_name)

    result = await validator.validate(admission_review)

    assert isinstance(result, ValidationResult)
    assert result.allowed is True, (
        f"Expected signed image to be allowed, but got: {result.messages}"
    )


@pytest.mark.asyncio
async def test_unsigned_image_with_key_verification(config, default_cosign_config):
    """Test that unsigned images are rejected with key verification."""
    validator = create_cosign_validator_with_config(config, default_cosign_config)
    image_name = f"{REGISTRY_URL}/test-app:unsigned"
    admission_review = create_admission_review(image_name)

    result = await validator.validate(admission_review)

    assert isinstance(result, ValidationResult)
    assert result.allowed is False, "Expected unsigned image to be rejected"
    assert len(result.messages) > 0
    assert any("invalid or missing signature" in msg.lower() for msg in result.messages), (
        f"Expected verification failure message, got: {result.messages}"
    )


@pytest.mark.asyncio
async def test_unsigned_image_with_disabled_verification(config, disabled_cosign_config):
    """Test that unsigned images are allowed when verification is disabled."""
    validator = create_cosign_validator_with_config(config, disabled_cosign_config)
    image_name = f"{REGISTRY_URL}/test-app:unsigned"
    admission_review = create_admission_review(image_name)

    result = await validator.validate(admission_review)

    assert isinstance(result, ValidationResult)
    assert result.allowed is True, (
        "Expected unsigned image to be allowed when verification disabled"
    )


@pytest.mark.asyncio
async def test_mixed_registry_configuration(config, mixed_registry_cosign_config):
    """Test different verification methods for different registries."""
    validator = create_cosign_validator_with_config(config, mixed_registry_cosign_config)

    # Test local registry (key verification required)
    local_image = f"{REGISTRY_URL}/test-app:signed"
    local_review = create_admission_review(local_image)
    result = await validator.validate(local_review)
    assert result.allowed is True, f"Expected signed local image to be allowed: {result.messages}"

    # Test Docker Hub image (verification disabled)
    docker_image = "nginx:latest"
    docker_review = create_admission_review(docker_image)
    result = await validator.validate(docker_review)
    assert result.allowed is True, "Expected Docker Hub image to be allowed (verification disabled)"


@pytest.mark.asyncio
async def test_wrong_signature_with_key_verification(config, default_cosign_config):
    """Test that images signed with wrong key are rejected."""
    validator = create_cosign_validator_with_config(config, default_cosign_config)
    image_name = f"{REGISTRY_URL}/test-app:wrongsig"
    admission_review = create_admission_review(image_name)

    result = await validator.validate(admission_review)

    assert isinstance(result, ValidationResult)
    assert result.allowed is False, "Expected image with wrong signature to be rejected"
    assert len(result.messages) > 0
    assert any("invalid or missing signature" in msg.lower() for msg in result.messages), (
        f"Expected signature verification failure, got: {result.messages}"
    )


@pytest.mark.asyncio
async def test_invalid_public_key_path(config):
    """Test handling of invalid public key path."""
    config_data = {
        "registries": [
            {
                "registry": REGISTRY_URL,
                "require_signature": True,
                "verification_method": "key",
                "public_key": str(NONEXISTENT_KEY_PATH),
            }
        ]
    }
    config_file = create_cosign_config_file(config_data)
    cosign_config = CosignConfig(cosign_registries_file=config_file)

    validator = create_cosign_validator_with_config(config, cosign_config)
    image_name = f"{REGISTRY_URL}/test-app:signed"
    admission_review = create_admission_review(image_name)

    result = await validator.validate(admission_review)

    assert isinstance(result, ValidationResult)
    assert result.allowed is False, "Expected invalid key path to cause rejection"
    assert len(result.messages) > 0


@pytest.mark.asyncio
async def test_no_registry_configuration_match(config):
    """Test behavior when no registry configuration matches."""
    # Create config with only docker.io registry
    config_data = {
        "registries": [
            {"registry": "docker.io", "require_signature": False, "verification_method": "disabled"}
        ]
    }
    config_file = create_cosign_config_file(config_data)
    cosign_config = CosignConfig(cosign_registries_file=config_file)

    validator = create_cosign_validator_with_config(config, cosign_config)

    # Test with localhost registry (no matching config)
    image_name = f"{REGISTRY_URL}/test-app:signed"
    admission_review = create_admission_review(image_name)

    result = await validator.validate(admission_review)

    # Should be allowed since no configuration means skip verification
    assert isinstance(result, ValidationResult)
    assert result.allowed is True, "Expected image to be allowed when no registry config matches"


@pytest.mark.asyncio
async def test_keyless_verification_configuration():
    """Test keyless verification configuration (mock test since we don't have actual keyless images)."""
    config_data = {
        "registries": [
            {
                "registry": "gcr.io",
                "require_signature": True,
                "verification_method": "keyless",
                "keyless_identity_regex": "^https://github.com/myorg/.*",
                "keyless_issuer": "https://token.actions.githubusercontent.com",
            }
        ]
    }
    config_file = create_cosign_config_file(config_data)
    cosign_config = CosignConfig(cosign_registries_file=config_file)

    # Verify the configuration is loaded correctly
    registry_config = cosign_config.get_registry_config("gcr.io")
    assert registry_config is not None
    assert registry_config.verification_method == "keyless"
    assert registry_config.keyless_identity_regex == "^https://github.com/myorg/.*"
    assert registry_config.keyless_issuer == "https://token.actions.githubusercontent.com"


@pytest.mark.asyncio
async def test_mixed_containers_with_different_registry_policies(
    config, mixed_registry_cosign_config
):
    """Test pod with containers from different registries with different policies."""
    validator = create_cosign_validator_with_config(config, mixed_registry_cosign_config)

    admission_review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-mixed-registries",
            "operation": "CREATE",
            "namespace": "default",
            "name": "mixed-registry-pod",
            "kind": {"kind": "Pod", "version": "v1", "group": ""},
            "object": {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "mixed-registry-pod", "namespace": "default"},
                "spec": {
                    "containers": [
                        {
                            "name": "local-container",
                            "image": f"{REGISTRY_URL}/test-app:signed",  # Key verification required
                            "resources": {"limits": {"memory": "256Mi"}},
                        },
                        {
                            "name": "docker-container",
                            "image": "nginx:latest",  # Verification disabled
                            "resources": {"limits": {"memory": "256Mi"}},
                        },
                    ]
                },
            },
        },
    }

    result = await validator.validate(admission_review)

    assert isinstance(result, ValidationResult)
    assert result.allowed is True, "Expected mixed registry pod to be allowed"


@pytest.mark.asyncio
async def test_default_fallback_configuration():
    """Test that default configuration is used when no config file exists."""
    # Create CosignConfig without specifying config file (should use default)
    cosign_config = CosignConfig()

    # Should have exactly one default configuration
    assert len(cosign_config.registry_configs) == 1
    assert cosign_config.registry_configs[0].registry == "*"
    assert cosign_config.registry_configs[0].verification_method == "key"
    assert cosign_config.registry_configs[0].require_signature is True


def test_cosign_config_loading():
    """Test cosign configuration loading from JSON file."""
    config_data = {
        "registries": [
            {
                "registry": "localhost:5000",
                "require_signature": True,
                "verification_method": "key",
                "public_key": str(COSIGN_KEY_PATH),
            },
            {
                "registry": "docker.io/*",
                "require_signature": False,
                "verification_method": "disabled",
            },
        ]
    }

    config_file = create_cosign_config_file(config_data)
    cosign_config = CosignConfig(cosign_registries_file=config_file)

    # Verify configurations were loaded
    assert len(cosign_config.registry_configs) == 2

    # Test localhost:5000 config
    local_config = cosign_config.get_registry_config("localhost:5000")
    assert local_config is not None
    assert local_config.verification_method == "key"
    assert local_config.require_signature is True
    assert local_config.public_key == COSIGN_KEY_PATH

    # Test docker.io config
    docker_config = cosign_config.get_registry_config("docker.io")
    assert docker_config is not None
    assert docker_config.verification_method == "disabled"
    assert docker_config.require_signature is False


def test_registry_pattern_matching():
    """Test registry pattern matching logic."""
    config_data = {
        "registries": [
            {
                "registry": "docker.io/*",
                "require_signature": False,
                "verification_method": "disabled",
            },
            {
                "registry": "gcr.io",
                "require_signature": True,
                "verification_method": "key",
                "public_key": str(COSIGN_KEY_PATH),
            },
            {
                "registry": "*",
                "require_signature": True,
                "verification_method": "key",
                "public_key": str(COSIGN_KEY_PATH),
            },
        ]
    }

    config_file = create_cosign_config_file(config_data)
    cosign_config = CosignConfig(cosign_registries_file=config_file)

    # Test exact match
    config = cosign_config.get_registry_config("gcr.io")
    assert config.registry == "gcr.io"

    # Test pattern match
    config = cosign_config.get_registry_config("docker.io")
    assert config.registry == "docker.io/*"

    # Test wildcard fallback
    config = cosign_config.get_registry_config("unknown.registry.com")
    assert config.registry == "*"


@pytest.mark.asyncio
async def test_service_resource_still_skipped(config, default_cosign_config):
    """Test that non-pod resources are still skipped with new config system."""
    validator = create_cosign_validator_with_config(config, default_cosign_config)

    admission_review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-service",
            "operation": "CREATE",
            "namespace": "default",
            "kind": {"kind": "Service", "version": "v1", "group": ""},
            "object": {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "test-service"},
                "spec": {"selector": {"app": "test"}, "ports": [{"port": 80}]},
            },
        },
    }

    result = await validator.validate(admission_review)

    assert isinstance(result, ValidationResult)
    assert result.allowed is True, "Expected Service to be allowed (skipped)"
    assert len(result.messages) == 0


def test_prerequisites_verification():
    """Test that all prerequisites are available (registry, OPA, test images)."""
    # Check registry
    result = subprocess.run(
        ["curl", "-s", f"http://{REGISTRY_URL}/v2/_catalog"],
        check=True,
        capture_output=True,
        text=True,
    )

    catalog = json.loads(result.stdout)
    repositories = catalog.get("repositories", [])

    # Check that our test images are available
    assert "test-app" in repositories, (
        f"Expected repository test-app not found in catalog: {repositories}"
    )

    # Check tags for test-app
    result = subprocess.run(
        ["curl", "-s", f"http://{REGISTRY_URL}/v2/test-app/tags/list"],
        check=True,
        capture_output=True,
        text=True,
    )

    tags_info = json.loads(result.stdout)
    tags = tags_info.get("tags", [])

    expected_tags = ["signed", "unsigned", "wrongsig"]
    for tag in expected_tags:
        assert tag in tags, f"Expected tag {tag} not found in available tags: {tags}"

    # Check cosign keys
    assert COSIGN_KEY_PATH.exists(), f"Cosign public key not found at {COSIGN_KEY_PATH}"
    assert WRONG_KEY_PATH.exists(), f"Wrong cosign public key not found at {WRONG_KEY_PATH}"

    print(f"✓ All prerequisites verified:")
    print(f"  - Registry: {REGISTRY_URL}")
    print(f"  - OPA: localhost:8181")
    print(f"  - Test images: {tags}")
    print(f"  - Cosign keys: {COSIGN_KEY_PATH.parent}")
