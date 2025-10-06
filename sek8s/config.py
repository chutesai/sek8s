"""
Configuration management for admission controller using Pydantic v2.
"""

from typing import List, Optional, Dict, Literal, Any
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import json
import logging

logger = logging.getLogger(__name__)


class NamespacePolicy(BaseSettings):
    """Policy configuration for a namespace."""

    mode: Literal["enforce", "warn", "monitor"] = "enforce"
    exempt: bool = False

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False
    )


class ServerConfig(BaseSettings):
    # Server configuration
    bind_address: str = Field(default="127.0.0.1", alias="ADMISSION_BIND_ADDRESS")
    port: int = Field(default=8443, alias="ADMISSION_PORT", ge=1, le=65535)

    # TLS configuration
    tls_cert_path: Optional[Path] = Field(default=None, alias="TLS_CERT_PATH")
    tls_key_path: Optional[Path] = Field(default=None, alias="TLS_KEY_PATH")

    # Debug mode
    debug: bool = Field(default=False, alias="DEBUG")


class AdmissionConfig(ServerConfig):
    """Main configuration for admission controller using Pydantic v2."""

    # OPA configuration
    opa_url: str = Field(default="http://localhost:8181", alias="OPA_URL")
    opa_timeout: float = Field(default=5.0, alias="OPA_TIMEOUT", gt=0)

    # Policy configuration
    policy_path: Path = Field(default=Path("/etc/opa/policies"), alias="POLICY_PATH")

    # Registry allowlist - expects JSON array from environment
    allowed_registries: List[str] = Field(
        default=["docker.io", "gcr.io", "quay.io", "localhost:30500"],
        alias="ALLOWED_REGISTRIES",
        description="JSON array of allowed registries",
    )

    # Cache configuration
    cache_enabled: bool = Field(default=True, alias="CACHE_ENABLED")
    cache_ttl: int = Field(default=300, alias="CACHE_TTL", ge=0)

    # Enforcement configuration
    enforcement_mode: Literal["enforce", "warn", "monitor"] = Field(
        default="enforce", alias="ENFORCEMENT_MODE"
    )

    # Namespace policies - expects JSON object from environment
    namespace_policies: Dict[str, NamespacePolicy] = Field(
        default={
            "kube-system": NamespacePolicy(mode="warn", exempt=False),
            "kube-public": NamespacePolicy(mode="warn", exempt=False),
            "kube-node-lease": NamespacePolicy(mode="warn", exempt=False),
            "gpu-operator": NamespacePolicy(mode="warn", exempt=False),
            "chutes": NamespacePolicy(mode="enforce", exempt=False),
            "default": NamespacePolicy(mode="enforce", exempt=False),
        },
        alias="NAMESPACE_POLICIES",
        description="JSON object of namespace policies",
    )

    # Metrics configuration
    metrics_enabled: bool = Field(default=True, alias="METRICS_ENABLED")

    # Config file support
    config_file: Optional[Path] = Field(default=None, alias="CONFIG_FILE")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="",  # No prefix since we use aliases
        populate_by_name=True,  # Allow both field name and alias
        use_enum_values=True,
        validate_assignment=True,  # Validate on assignment
        extra="ignore",  # Ignore extra fields
    )

    @field_validator("namespace_policies", mode="before")
    @classmethod
    def parse_namespace_policies(cls, v: Any) -> Dict[str, NamespacePolicy]:
        """Parse namespace policies from dict, ensuring NamespacePolicy objects."""
        if isinstance(v, dict):
            return {
                ns: NamespacePolicy(**policy) if isinstance(policy, dict) else policy
                for ns, policy in v.items()
            }
        return v

    @field_validator("tls_cert_path", "tls_key_path", mode="after")
    @classmethod
    def validate_tls_paths(cls, v: Optional[Path]) -> Optional[Path]:
        """Validate that TLS paths exist if specified."""
        if v is not None and not v.exists():
            raise ValueError(f"TLS path does not exist: {v}")
        return v

    @field_validator("policy_path", mode="after")
    @classmethod
    def validate_policy_path(cls, v: Path) -> Path:
        """Ensure policy path exists or can be created."""
        if not v.exists():
            v.mkdir(parents=True, exist_ok=True)
        return v

    def __init__(self, **kwargs):
        """Initialize config with support for config file."""
        # Check if config file is specified
        config_file_path = kwargs.get("config_file") or kwargs.get("CONFIG_FILE")
        if not config_file_path:
            config_file_path = Path("/etc/admission-controller/config.json")

        # Load from config file if it exists
        file_config = {}
        if config_file_path and Path(config_file_path).exists():
            with open(config_file_path, "r") as f:
                file_config = json.load(f)

        # Merge configurations (kwargs take precedence over file)
        merged_config = {**file_config, **kwargs}

        super().__init__(**merged_config)

    def get_namespace_policy(self, namespace: str) -> NamespacePolicy:
        """Get policy for a specific namespace."""
        if namespace in self.namespace_policies:
            return self.namespace_policies[namespace]
        return self.namespace_policies.get("default", NamespacePolicy())

    def is_namespace_exempt(self, namespace: str) -> bool:
        """Check if namespace is exempt from admission control."""
        policy = self.get_namespace_policy(namespace)
        return policy.exempt

    def export_json(self) -> str:
        """Export configuration as JSON."""
        return self.model_dump_json(indent=2, exclude_unset=False)

    def export_dict(self) -> dict:
        """Export configuration as dictionary."""
        return self.model_dump(exclude_unset=False)


class OPAConfig(BaseSettings):
    """Configuration specific to OPA."""

    opa_binary_path: Path = Field(default=Path("/usr/local/bin/opa"), alias="OPA_BINARY_PATH")
    opa_log_level: Literal["debug", "info", "warn", "error"] = Field(
        default="info", alias="OPA_LOG_LEVEL"
    )
    opa_decision_logs: bool = Field(default=False, alias="OPA_DECISION_LOGS")
    opa_diagnostic_addr: str = Field(default="0.0.0.0:8282", alias="OPA_DIAGNOSTIC_ADDR")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="",
        populate_by_name=True,
    )


class CosignRegistryConfig(BaseSettings):
    """Configuration for cosign verification per registry."""

    # Registry pattern (can include wildcards)
    registry: str

    # Whether signature verification is required
    require_signature: bool = True

    # Verification method
    verification_method: Literal["key", "keyless", "disabled"] = "key"

    # Public key path for key-based verification
    public_key: Optional[Path] = None

    # Keyless verification settings
    keyless_identity_regex: Optional[str] = None
    keyless_issuer: Optional[str] = None

    # Rekor transparency log URL
    rekor_url: str = "https://rekor.sigstore.dev"

    # Fulcio CA URL for keyless verification
    fulcio_url: str = "https://fulcio.sigstore.dev"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False
    )


class CosignConfig(BaseSettings):
    """Configuration for Cosign integration (Phase 4b)."""

    cache_ttl: int = Field(default=3600, ge=0)

    # Cosign config
    oidc_identity_regex: str = Field(default="^https://github.com/your-org/.*")
    oidc_issuer: str = Field(default="https://token.actions.githubusercontent.com")
    cosign_rekor_url: str = Field(default="https://rekor.sigstore.dev")
    fulcio_url: str = Field(default="https://fulcio.sigstore.dev")

    # Registry configurations - loaded from file or defaults
    registry_configs: List[CosignRegistryConfig] = Field(
        default_factory=list, description="List of cosign configurations per registry"
    )

    # Cosign config file path
    config_file: Optional[Path] = Field(
        default=None, description="Path to cosign registry configuration JSON file"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="COSIGN_",
        populate_by_name=True,
    )

    @field_validator("registry_configs", mode="before")
    @classmethod
    def parse_registry_configs(cls, v: Any) -> List[CosignRegistryConfig]:
        """Parse cosign registry configs from list of dicts."""
        if isinstance(v, list):
            configs = []
            for item in v:
                if isinstance(item, dict):
                    configs.append(CosignRegistryConfig(**item))
                elif isinstance(item, CosignRegistryConfig):
                    configs.append(item)
            return configs
        return v

    def __init__(self, **kwargs):
        """Initialize cosign config with support for config file."""
        super().__init__(**kwargs)

        # Load registry configs after initialization
        self._load_registry_configs()

    def _load_registry_configs(self) -> None:
        """Load registry configurations from file or use defaults."""
        # If configs are already populated, don't override
        if self.registry_configs:
            logger.info(f"Using {len(self.registry_configs)} pre-configured registry configs")
            return

        # Try to load from config file
        config_file_path = self.config_file
        if not config_file_path:
            config_file_path = Path("/etc/admission-controller/cosign-registries.json")

        if config_file_path and config_file_path.exists():
            try:
                with open(config_file_path, "r") as f:
                    config_data = json.load(f)

                # Handle different JSON structures
                if isinstance(config_data, dict) and "registries" in config_data:
                    registry_data = config_data["registries"]
                elif isinstance(config_data, list):
                    registry_data = config_data
                else:
                    logger.warning(f"Invalid cosign config format in {config_file_path}")
                    registry_data = []

                # Parse registry configurations
                configs = []
                for item in registry_data:
                    if isinstance(item, dict):
                        # Convert string paths to Path objects
                        if "public_key" in item and item["public_key"]:
                            item["public_key"] = Path(item["public_key"])
                        configs.append(CosignRegistryConfig(**item))

                self.registry_configs = configs
                logger.info(f"Loaded {len(configs)} registry configs from {config_file_path}")

            except Exception as e:
                logger.error(f"Failed to load cosign config from {config_file_path}: {e}")
        else:
            # Fall back to default configuration
            logger.info("Using default cosign registry configuration")
            self.registry_configs = [
                CosignRegistryConfig(
                    registry="*",
                    require_signature=True,
                    verification_method="key",
                    public_key=Path("/root/.cosign/cosign.pub"),
                )
            ]

    def get_registry_config(self, registry: str) -> Optional[CosignRegistryConfig]:
        """Get cosign configuration for a specific registry."""
        # First try exact matches
        for config in self.registry_configs:
            if config.registry == registry:
                return config

        # Then try pattern matches (simple wildcard support)
        for config in self.registry_configs:
            if self._matches_pattern(registry, config.registry):
                return config

        # Finally, look for wildcard default
        for config in self.registry_configs:
            if config.registry == "*":
                return config

        return None

    def _matches_pattern(self, registry: str, pattern: str) -> bool:
        """Simple pattern matching for registry names."""
        if pattern == "*":
            return True

        if "*" in pattern:
            # Simple wildcard matching
            if pattern.endswith("/*"):
                prefix = pattern[:-2]
                return registry.startswith(prefix)
            elif pattern.startswith("*/"):
                suffix = pattern[2:]
                return registry.endswith(suffix)

        return registry == pattern


# For backward compatibility and convenience
def load_config(**kwargs) -> AdmissionConfig:
    """Load configuration with environment variables and optional overrides."""
    return AdmissionConfig(**kwargs)
