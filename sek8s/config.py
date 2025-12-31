"""
Configuration management for admission controller using Pydantic v2.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    bind_address: str = Field(default="127.0.0.1")
    port: int = Field(default=8443, ge=1, le=65535)
    uds_path: Optional[Path] = Field(default=None)
    require_tls: bool = Field(default=True, alias="REQUIRE_TLS")

    # TLS configuration
    tls_cert_path: Optional[Path] = Field(default=None, alias="TLS_CERT_PATH")
    tls_key_path: Optional[Path] = Field(default=None, alias="TLS_KEY_PATH")
    client_ca_path: Optional[Path] = Field(default=None, alias="CLIENT_CA_PATH")
    mtls_required: bool = Field(default=False, alias="MTLS_REQUIRED")

    # Debug mode
    debug: bool = Field(default=False, alias="DEBUG")

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="",
        extra='ignore'
    )

    @field_validator("uds_path", mode="before")
    @classmethod
    def normalize_empty_uds(cls, v: Optional[str | Path]) -> Optional[Path | str]:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("tls_cert_path", "tls_key_path", "client_ca_path", mode="before")
    @classmethod
    def normalize_empty_tls(cls, v: Optional[str | Path]) -> Optional[str | Path]:
        if v is None:
            return None
        if isinstance(v, str):
            if not v.strip():
                return None
            return v
        return v

    @field_validator("tls_cert_path", "tls_key_path", "client_ca_path", mode="after")
    @classmethod
    def validate_paths(cls, v: Optional[Path]) -> Optional[Path]:
        """Validate that paths exist if specified."""
        if v and not v.exists():
            raise ValueError(f"Path does not exist: {v}")
        return v
    
    @field_validator("uds_path", mode="after")
    @classmethod
    def validate_uds_directory(cls, v: Optional[Path]) -> Optional[Path]:
        """Validate that parent dir exist if specified."""
        if v and not v.parent.exists():
            raise ValueError(f"Directory for UDS path does not exist: {v}")
        return v


class AuthConfig(ServerConfig):
    """Base configuration for services that require authentication.
    
    Services can inherit from this to get auth capabilities.
    Auth fields are optional by default - services can make them required if needed.
    """
    
    miner_ss58: Optional[str] = Field(default=None, alias="MINER_SS58")
    allowed_validators_str: Optional[str] = Field(default=None, alias="ALLOWED_VALIDATORS")
    
    _allowed_validators: Optional[list[str]] = None

    @property
    def allowed_validators(self) -> list[str]:
        """Parse comma-separated validator list."""
        if self._allowed_validators is None:
            if self.allowed_validators_str:
                self._allowed_validators = [
                    item.strip() 
                    for item in self.allowed_validators_str.split(',') 
                    if item.strip()
                ]
            else:
                self._allowed_validators = []
        return self._allowed_validators

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra='ignore'
    )

class AttestationServiceConfig(ServerConfig):

    hostname: str = os.getenv("HOSTNAME")

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra='ignore'
    )


class SystemStatusConfig(AuthConfig):
    """Configuration for the read-only system status service.
    
    Inherits auth capabilities from AuthConfig for endpoints like shutdown.
    """

    require_tls: bool = Field(default=False, alias="REQUIRE_TLS")

    max_output_bytes: int = Field(default=16_384, alias="MAX_OUTPUT_BYTES", ge=1024, le=1_000_000)
    command_timeout_seconds: float = Field(
        default=10.0,
        alias="COMMAND_TIMEOUT_SECONDS",
        gt=0.0,
        le=60.0,
    )
    log_tail_default: int = Field(default=200, alias="LOG_TAIL_DEFAULT", ge=1, le=5000)
    log_tail_max: int = Field(default=1000, alias="LOG_TAIL_MAX", ge=100, le=10_000)
    log_window_max_minutes: int = Field(
        default=1440,
        alias="LOG_WINDOW_MAX_MINUTES",
        ge=1,
        le=7 * 24 * 60,
    )

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra='ignore'
    )

class AttestationProxyConfig(AuthConfig):
    """Configuration for attestation proxy service.
    
    Requires auth fields to be configured.
    """

    # Override to make these required
    allowed_validators_str: str = Field(..., alias="ALLOWED_VALIDATORS")
    miner_ss58: str = Field(..., alias="MINER_SS58")

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra='ignore'
    )

class AdmissionConfig(ServerConfig):
    """Main configuration for admission controller using Pydantic v2."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra='ignore'
    )

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
    )


class CosignVerificationConfig(BaseSettings):
    """Base verification configuration that can be inherited at any level."""
    
    require_signature: bool = True
    verification_method: Literal["key", "keyless", "disabled"] = "key"
    
    # Key-based verification
    public_key: Optional[Path] = None
    
    # Keyless verification
    keyless_identity_regex: Optional[str] = None
    keyless_issuer: Optional[str] = None
    
    # Connection options
    allow_http: bool = False
    allow_insecure: bool = False
    
    # Transparency log
    rekor_url: str = "https://rekor.sigstore.dev"
    fulcio_url: str = "https://fulcio.sigstore.dev"
    
    model_config = SettingsConfigDict(case_sensitive=False)


class CosignRepositoryConfig(CosignVerificationConfig):
    """Repository-specific cosign configuration."""
    
    repository: str  # e.g., "chutes-agent" or "myapp"


class CosignOrganizationConfig(CosignVerificationConfig):
    """Organization-specific cosign configuration."""
    
    organization: str  # e.g., "parachutes", "bitnami", "library"
    
    # Optional repository-level overrides
    repositories: Dict[str, CosignRepositoryConfig] = Field(default_factory=dict)


class CosignRegistryConfig(CosignVerificationConfig):
    """Registry-level cosign configuration with nested org/repo structure."""
    
    registry: str  # e.g., "docker.io", "gcr.io", "registry.k8s.io"
    
    # Optional organization-level configs
    organizations: Dict[str, CosignOrganizationConfig] = Field(default_factory=dict)

class CosignConfig(BaseSettings):
    """Configuration for Cosign integration (Phase 4b)."""

    cache_ttl: int = Field(default=3600, ge=0)
    cache_maxsize: int = Field(default=1024, ge=1)
    negative_cache_ttl: int = Field(default=300, ge=0)
    rate_limit_backoff_seconds: int = Field(default=300, ge=0)

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
    cosign_registries_file: Optional[Path] = Field(
        default=None, description="Path to cosign registry configuration JSON file"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
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
        config_file_path = self.cosign_registries_file
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
                    public_key=Path("/etc/admission-controller/.cosign/cosign.pub"),
                )
            ]

    def get_verification_config(
        self, registry: str, organization: str, repository: str
    ) -> Optional[CosignVerificationConfig]:
        """
        Get the most specific verification config for an image.
        
        Precedence (most specific wins):
        1. Repository-level config (if specified)
        2. Organization-level config (if specified)
        3. Registry-level config (default for that registry)
        4. Wildcard registry (*) config
        
        Args:
            registry: Registry hostname (e.g., "docker.io", "gcr.io")
            organization: Organization/namespace (e.g., "parachutes", "library")
            repository: Repository name (e.g., "chutes-agent", "nginx")
        
        Returns:
            The most specific CosignVerificationConfig, or None if no config found
        """
        # Normalize registry name
        registry = self._normalize_registry_name(registry)
        
        logger.debug(f"Looking up config for {registry}/{organization}/{repository}")
        
        # Find matching registry config (exact match or pattern match)
        registry_config = None
        wildcard_config = None
        
        for config in self.registry_configs:
            if config.registry == registry:
                registry_config = config
                break
            elif config.registry == "*":
                wildcard_config = config
            elif self._matches_registry_pattern(registry, config.registry):
                registry_config = config
                break
        
        # Fall back to wildcard if no specific registry found
        if not registry_config:
            registry_config = wildcard_config
        
        if not registry_config:
            logger.warning(f"No registry config found for {registry}")
            return None
        
        # Start with registry-level defaults
        verification_config = registry_config
        
        # Check for organization-level override
        org_config = None
        if registry_config.organizations:
            # Try exact match first
            if organization in registry_config.organizations:
                org_config = registry_config.organizations[organization]
            else:
                # Try pattern matching
                for org_pattern, config in registry_config.organizations.items():
                    if self._matches_pattern(organization, org_pattern):
                        org_config = config
                        break
            
            if org_config:
                logger.debug(f"Found org-level config for {organization}")
                verification_config = org_config
                
                # Check for repository-level override within this org
                if org_config.repositories:
                    # Try exact match first
                    if repository in org_config.repositories:
                        repo_config = org_config.repositories[repository]
                        logger.debug(f"Found repo-level config for {repository}")
                        verification_config = repo_config
                    else:
                        # Try pattern matching
                        for repo_pattern, config in org_config.repositories.items():
                            if self._matches_pattern(repository, repo_pattern):
                                logger.debug(f"Found repo-level config for {repository} via pattern {repo_pattern}")
                                verification_config = config
                                break
        
        logger.debug(
            f"Using {verification_config.__class__.__name__} for {registry}/{organization}/{repository}: "
            f"method={verification_config.verification_method}, "
            f"require_signature={verification_config.require_signature}"
        )
        
        return verification_config

    def _normalize_registry_name(self, registry: str) -> str:
        """Normalize registry name for consistent matching."""
        # Remove protocol if present
        if registry.startswith(("http://", "https://")):
            registry = urlparse(registry).netloc
        
        # Handle Docker Hub special cases
        if registry in ["docker.io", "registry-1.docker.io", "index.docker.io"]:
            return "docker.io"
        
        return registry.lower()

    def _matches_registry_pattern(self, registry: str, pattern: str) -> bool:
        """Match registry against a pattern."""
        if pattern == "*":
            return True
        
        registry = registry.lower()
        pattern = pattern.lower()
        
        # Simple wildcard support
        if "*" in pattern:
            if pattern.endswith("*"):
                # Prefix match: gcr.io* matches gcr.io, gcr.io.local, etc.
                prefix = pattern[:-1]
                return registry.startswith(prefix)
            elif pattern.startswith("*"):
                # Suffix match: *.gcr.io matches us.gcr.io, eu.gcr.io, etc.
                suffix = pattern[1:]
                return registry.endswith(suffix)
        
        return registry == pattern

    def _matches_pattern(self, value: str, pattern: str) -> bool:
        """
        Simple pattern matching for organizations and repositories.
        
        Supports:
        - Exact match: "parachutes" matches "parachutes"
        - Prefix match: "google/*" matches "google/anything"
        - Suffix match: "*/base" matches "anything/base"
        - Wildcard: "*" matches everything
        """
        if pattern == "*":
            return True
        
        value = value.lower()
        pattern = pattern.lower()
        
        if "*" not in pattern:
            # Exact match
            return value == pattern
        
        if pattern.endswith("/*"):
            # Prefix match: "google/*" matches "google/cloud-sdk", "google/anything"
            prefix = pattern[:-2]
            return value.startswith(prefix + "/") or value == prefix
        
        if pattern.startswith("*/"):
            # Suffix match: "*/base" matches "distroless/base", "alpine/base"
            suffix = pattern[2:]
            return value.endswith("/" + suffix) or value == suffix
        
        if pattern == "*/*":
            # Match anything with at least one slash
            return "/" in value
        
        # For more complex patterns, you could use fnmatch or regex
        # For now, just do simple wildcard replacement
        import fnmatch
        return fnmatch.fnmatch(value, pattern)


# For backward compatibility and convenience
def load_config(**kwargs) -> AdmissionConfig:
    """Load configuration with environment variables and optional overrides."""
    return AdmissionConfig(**kwargs)
