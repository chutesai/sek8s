# tests/unit/test_config.py
"""
Unit tests for Pydantic configuration
"""

import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, mock_open

from sek8s.config import AdmissionConfig, AttestationProxyConfig, NamespacePolicy, OPAConfig, CosignConfig, load_config


class TestAdmissionConfig:
    """Test AdmissionConfig with Pydantic v2 JSON format."""

    def setup_method(self):
        """Clear environment before each test."""
        env_vars = [
            "ADMISSION_BIND_ADDRESS",
            "ADMISSION_PORT",
            "TLS_CERT_PATH",
            "TLS_KEY_PATH",
            "OPA_URL",
            "ALLOWED_REGISTRIES",
            "NAMESPACE_POLICIES",
            "DEBUG",
            "ENFORCEMENT_MODE",
            "CACHE_TTL",
        ]
        for var in env_vars:
            os.environ.pop(var, None)

    def test_default_config(self):
        """Test default configuration values."""
        config = AdmissionConfig()

        assert config.bind_address == "127.0.0.1"
        assert config.port == 8443
        assert config.allowed_registries == ["docker.io", "gcr.io", "quay.io", "localhost:30500"]
        assert config.enforcement_mode == "enforce"
        assert config.debug is False

    def test_allowed_registries_json_parsing(self):
        """Test parsing of JSON array for allowed_registries."""
        # Set as JSON array (Pydantic v2 default behavior)
        os.environ["ALLOWED_REGISTRIES"] = '["docker.io", "gcr.io", "quay.io"]'

        config = AdmissionConfig()

        assert config.allowed_registries == ["docker.io", "gcr.io", "quay.io"]

    def test_allowed_registries_with_wildcards(self):
        """Test registry list with wildcards."""
        os.environ["ALLOWED_REGISTRIES"] = '["docker.io", "*.amazonaws.com", "*.azurecr.io"]'

        config = AdmissionConfig()

        assert config.allowed_registries == ["docker.io", "*.amazonaws.com", "*.azurecr.io"]

    def test_namespace_policies_json_parsing(self):
        """Test parsing of JSON object for namespace_policies."""
        policies = {
            "kube-system": {"mode": "warn", "exempt": False},
            "production": {"mode": "enforce", "exempt": False},
            "development": {"mode": "monitor", "exempt": True},
        }

        os.environ["NAMESPACE_POLICIES"] = json.dumps(policies)

        config = AdmissionConfig()

        assert "kube-system" in config.namespace_policies
        assert config.namespace_policies["kube-system"].mode == "warn"
        assert config.namespace_policies["kube-system"].exempt is False

        assert "production" in config.namespace_policies
        assert config.namespace_policies["production"].mode == "enforce"

        assert "development" in config.namespace_policies
        assert config.namespace_policies["development"].exempt is True

    def test_boolean_parsing(self):
        """Test boolean environment variable parsing."""
        # Test various boolean representations
        for true_val in ["true", "True", "TRUE", "1"]:
            os.environ["DEBUG"] = true_val
            config = AdmissionConfig()
            assert config.debug is True

        for false_val in ["false", "False", "FALSE", "0"]:
            os.environ["DEBUG"] = false_val
            config = AdmissionConfig()
            assert config.debug is False

    def test_port_validation(self):
        """Test port range validation."""
        # Valid port
        os.environ["ADMISSION_PORT"] = "9000"
        config = AdmissionConfig()
        assert config.port == 9000

        # Invalid port (too high)
        os.environ["ADMISSION_PORT"] = "70000"
        with pytest.raises(ValueError):
            AdmissionConfig()

        # Invalid port (too low)
        os.environ["ADMISSION_PORT"] = "0"
        with pytest.raises(ValueError):
            AdmissionConfig()

    def test_enforcement_mode_validation(self):
        """Test enforcement mode enum validation."""
        # Valid modes
        for mode in ["enforce", "warn", "monitor"]:
            os.environ["ENFORCEMENT_MODE"] = mode
            config = AdmissionConfig()
            assert config.enforcement_mode == mode

        # Invalid mode
        os.environ["ENFORCEMENT_MODE"] = "invalid"
        with pytest.raises(ValueError):
            AdmissionConfig()

    def test_config_file_loading(self):
        """Test loading from JSON config file."""
        import tempfile

        config_data = {
            "bind_address": "0.0.0.0",
            "port": 8080,
            "allowed_registries": ["custom.registry.com"],
            "enforcement_mode": "warn",
            "namespace_policies": {"custom-ns": {"mode": "monitor", "exempt": True}},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            config_file = f.name

        try:
            # Load with config file
            config = AdmissionConfig(config_file=config_file)

            assert config.bind_address == "0.0.0.0"
            assert config.port == 8080
            assert config.allowed_registries == ["custom.registry.com"]
            assert config.enforcement_mode == "warn"
            assert "custom-ns" in config.namespace_policies
        finally:
            os.unlink(config_file)

    def test_env_overrides_config_file(self):
        """Test that environment variables override config file."""
        import tempfile

        config_data = {"port": 8080, "debug": False}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            config_file = f.name

        try:
            # Set environment variable that should override file
            os.environ["ADMISSION_PORT"] = "9443"
            os.environ["DEBUG"] = "true"

            config = AdmissionConfig(config_file=config_file)

            # Environment should win
            assert config.port == 9443
            assert config.debug is True
        finally:
            os.unlink(config_file)

    def test_export_methods(self):
        """Test configuration export methods."""
        os.environ["ALLOWED_REGISTRIES"] = '["test.registry.com"]'
        os.environ["DEBUG"] = "true"

        config = AdmissionConfig()

        # Test JSON export
        json_str = config.export_json()
        parsed = json.loads(json_str)
        assert parsed["allowed_registries"] == ["test.registry.com"]
        assert parsed["debug"] is True

        # Test dict export
        dict_export = config.export_dict()
        assert dict_export["allowed_registries"] == ["test.registry.com"]
        assert dict_export["debug"] is True

    def test_get_namespace_policy(self):
        """Test getting namespace-specific policies."""
        policies = {
            "production": {"mode": "enforce", "exempt": False},
            "development": {"mode": "monitor", "exempt": True},
        }

        os.environ["NAMESPACE_POLICIES"] = json.dumps(policies)
        config = AdmissionConfig()

        # Get existing namespace
        prod_policy = config.get_namespace_policy("production")
        assert prod_policy.mode == "enforce"
        assert prod_policy.exempt is False

        # Get non-existent namespace (should return default)
        unknown_policy = config.get_namespace_policy("unknown")
        assert unknown_policy.mode == "enforce"  # default mode
        assert unknown_policy.exempt is False

        # Test is_namespace_exempt
        assert config.is_namespace_exempt("development") is True
        assert config.is_namespace_exempt("production") is False


class TestNamespacePolicy:
    """Tests for NamespacePolicy."""

    def test_default_namespace_policy(self):
        """Test default namespace policy values."""
        policy = NamespacePolicy()

        assert policy.mode == "enforce"
        assert policy.exempt is False

    def test_custom_namespace_policy(self):
        """Test custom namespace policy."""
        policy = NamespacePolicy(mode="warn", exempt=True)

        assert policy.mode == "warn"
        assert policy.exempt is True

    def test_invalid_mode(self):
        """Test invalid enforcement mode."""
        with pytest.raises(ValueError):
            NamespacePolicy(mode="invalid")


class TestOPAConfig:
    """Tests for OPAConfig."""

    def test_default_opa_config(self):
        """Test default OPA configuration."""
        config = OPAConfig()

        assert config.opa_binary_path == Path("/usr/local/bin/opa")
        assert config.opa_log_level == "info"
        assert config.opa_decision_logs is False
        assert config.opa_diagnostic_addr == "0.0.0.0:8282"

    def test_opa_config_env_override(self):
        """Test OPA config environment overrides."""
        os.environ["OPA_BINARY_PATH"] = "/custom/opa"
        os.environ["OPA_LOG_LEVEL"] = "debug"
        os.environ["OPA_DECISION_LOGS"] = "true"

        config = OPAConfig()

        assert config.opa_binary_path == Path("/custom/opa")
        assert config.opa_log_level == "debug"
        assert config.opa_decision_logs is True

        # Cleanup
        del os.environ["OPA_BINARY_PATH"]
        del os.environ["OPA_LOG_LEVEL"]
        del os.environ["OPA_DECISION_LOGS"]

    def test_invalid_log_level(self):
        """Test invalid OPA log level."""
        with pytest.raises(ValueError):
            OPAConfig(opa_log_level="invalid")


class TestCosignConfig:
    """Tests for CosignConfig."""

    def test_default_cosign_config(self):
        """Test default Cosign configuration."""
        config = CosignConfig()

        assert config.cache_ttl == 3600
        assert config.oidc_identity_regex == "^https://github.com/your-org/.*"
        assert config.oidc_issuer == "https://token.actions.githubusercontent.com"
        assert config.cosign_rekor_url == "https://rekor.sigstore.dev"
        assert config.fulcio_url == "https://fulcio.sigstore.dev"

        # Should have exactly one default registry configuration
        assert len(config.registry_configs) == 1
        default_registry = config.registry_configs[0]
        assert default_registry.registry == "*"
        assert default_registry.require_signature is True
        assert default_registry.verification_method == "key"
        assert default_registry.public_key == Path("/etc/admission-controller/.cosign/cosign.pub")

    def test_cosign_config_from_env(self):
        """Test Cosign config with environment variables."""
        os.environ["COSIGN_CACHE_TTL"] = "7200"
        os.environ["COSIGN_OIDC_IDENTITY_REGEX"] = "^https://github.com/myorg/.*"
        os.environ["COSIGN_OIDC_ISSUER"] = "https://custom.issuer.com"

        try:
            config = CosignConfig()

            assert config.cache_ttl == 7200
            assert config.oidc_identity_regex == "^https://github.com/myorg/.*"
            assert config.oidc_issuer == "https://custom.issuer.com"
        finally:
            # Cleanup
            for var in ["COSIGN_CACHE_TTL", "COSIGN_OIDC_IDENTITY_REGEX", "COSIGN_OIDC_ISSUER"]:
                os.environ.pop(var, None)

    def test_cosign_config_with_registry_configs_list(self):
        """Test Cosign config with registry configurations from list."""
        registry_configs = [
            {
                "registry": "localhost:5000",
                "require_signature": True,
                "verification_method": "key",
                "public_key": "/path/to/key.pub",
            },
            {
                "registry": "docker.io",
                "require_signature": False,
                "verification_method": "disabled",
            },
        ]

        with patch("pathlib.Path.exists", return_value=True):
            config = CosignConfig(registry_configs=registry_configs)

            assert len(config.registry_configs) == 2

            # Check first config
            local_config = config.registry_configs[0]
            assert local_config.registry == "localhost:5000"
            assert local_config.require_signature is True
            assert local_config.verification_method == "key"
            assert local_config.public_key == Path("/path/to/key.pub")

            # Check second config
            docker_config = config.registry_configs[1]
            assert docker_config.registry == "docker.io"
            assert docker_config.require_signature is False
            assert docker_config.verification_method == "disabled"

    def test_cosign_config_with_config_file(self):
        """Test Cosign config loading from JSON file."""
        import tempfile

        config_data = {
            "registries": [
                {
                    "registry": "gcr.io",
                    "require_signature": True,
                    "verification_method": "keyless",
                    "keyless_identity_regex": "^https://github.com/.*",
                    "keyless_issuer": "https://token.actions.githubusercontent.com",
                },
                {
                    "registry": "localhost:5000",
                    "require_signature": True,
                    "verification_method": "key",
                    "public_key": "/test/key.pub",
                },
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as fh:
            json.dump(config_data, fh)
            fh.flush()
            config_file = fh.name

            with patch("pathlib.Path.exists", return_value=True):
                config = CosignConfig(cosign_registries_file=Path(config_file))

                assert len(config.registry_configs) == 2

                # Check keyless config
                gcr_config = None
                for reg_config in config.registry_configs:
                    if reg_config.registry == "gcr.io":
                        gcr_config = reg_config
                        break

                assert gcr_config is not None
                assert gcr_config.verification_method == "keyless"
                assert gcr_config.keyless_identity_regex == "^https://github.com/.*"
                assert gcr_config.keyless_issuer == "https://token.actions.githubusercontent.com"


    def test_get_registry_config_exact_match(self):
        """Test getting registry config with exact match."""
        registry_configs = [
            {
                "registry": "gcr.io",
                "require_signature": True,
                "verification_method": "key",
                "public_key": "/gcr/key.pub",
            },
            {
                "registry": "docker.io",
                "require_signature": False,
                "verification_method": "disabled",
            },
        ]

        with patch("pathlib.Path.exists", return_value=True):
            config = CosignConfig(registry_configs=registry_configs)

            gcr_config = config.get_registry_config("gcr.io")
            assert gcr_config is not None
            assert gcr_config.registry == "gcr.io"
            assert gcr_config.verification_method == "key"

            docker_config = config.get_registry_config("docker.io")
            assert docker_config is not None
            assert docker_config.verification_method == "disabled"

    def test_get_registry_config_pattern_match(self):
        """Test getting registry config with pattern matching."""
        registry_configs = [
            {
                "registry": "docker.io/*",
                "require_signature": False,
                "verification_method": "disabled",
            },
            {
                "registry": "*",
                "require_signature": True,
                "verification_method": "key",
                "public_key": "/default/key.pub",
            },
        ]

        with patch("pathlib.Path.exists", return_value=True):
            config = CosignConfig(registry_configs=registry_configs)

            # Should match pattern
            docker_config = config.get_registry_config("docker.io")
            assert docker_config is not None
            assert docker_config.registry == "docker.io/*"

            # Should match wildcard
            unknown_config = config.get_registry_config("unknown.registry.com")
            assert unknown_config is not None
            assert unknown_config.registry == "*"

    def test_get_registry_config_no_match(self):
        """Test getting registry config when no match found."""
        registry_configs = [
            {
                "registry": "gcr.io",
                "require_signature": True,
                "verification_method": "key",
                "public_key": "/gcr/key.pub",
            }
        ]

        with patch("pathlib.Path.exists", return_value=True):
            config = CosignConfig(registry_configs=registry_configs)

            # No match should return None
            result = config.get_registry_config("docker.io")
            assert result is None

    def test_cosign_registry_config_validation(self):
        """Test CosignRegistryConfig validation."""
        from sek8s.config import CosignRegistryConfig

        # Test valid key-based config
        with patch("pathlib.Path.exists", return_value=True):
            config = CosignRegistryConfig(
                registry="localhost:5000",
                require_signature=True,
                verification_method="key",
                public_key="/path/to/key.pub",
            )
            assert config.verification_method == "key"
            assert config.public_key == Path("/path/to/key.pub")

        # Test keyless config
        config = CosignRegistryConfig(
            registry="gcr.io",
            require_signature=True,
            verification_method="keyless",
            keyless_identity_regex="^https://github.com/.*",
            keyless_issuer="https://token.actions.githubusercontent.com",
        )
        assert config.verification_method == "keyless"
        assert config.keyless_identity_regex == "^https://github.com/.*"

        # Test disabled config
        config = CosignRegistryConfig(
            registry="docker.io", require_signature=False, verification_method="disabled"
        )
        assert config.verification_method == "disabled"
        assert config.require_signature is False

    def test_invalid_verification_method(self):
        """Test invalid verification method."""
        from sek8s.config import CosignRegistryConfig

        with pytest.raises(ValueError):
            CosignRegistryConfig(registry="test.registry", verification_method="invalid")


class TestLoadConfig:
    """Tests for load_config helper function."""

    def test_load_config_default(self):
        """Test load_config with defaults."""
        config = load_config()

        assert isinstance(config, AdmissionConfig)
        assert config.bind_address == "127.0.0.1"

    def test_load_config_with_overrides(self):
        """Test load_config with parameter overrides."""
        config = load_config(bind_address="0.0.0.0", port=9000, debug=True)

        assert config.bind_address == "0.0.0.0"
        assert config.port == 9000
        assert config.debug is True

class TestProxyConfig:

    def test_allowed_validator(self):
        os.environ["ALLOWED_VALIDATORS"] = "abcd1234"
        config = AttestationProxyConfig()

        assert len(config.allowed_validators) == 1
        assert config.allowed_validators[0] == "abcd1234"

    def test_allowed_validators(self):
        os.environ["ALLOWED_VALIDATORS"] = "abcd1234,efgh6789"
        config = AttestationProxyConfig()

        assert len(config.allowed_validators) == 2
        assert config.allowed_validators[0] == "abcd1234"
        assert config.allowed_validators[1] == "efgh6789"

    def test_miner_ss58(self):
        os.environ["ALLOWED_VALIDATORS"] = "abcd1234,efgh6789"
        os.environ["MINER_SS58"] = "abcd1234"
        config = AttestationProxyConfig()

        assert config.miner_ss58 == "abcd1234"