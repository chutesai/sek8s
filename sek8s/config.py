"""
Configuration management for admission controller using Pydantic.
"""

from typing import List, Optional, Dict, Literal
from pathlib import Path
from pydantic import Field, field_validator
import json

from pydantic_settings import BaseSettings


class NamespacePolicy(BaseSettings):
    """Policy configuration for a namespace."""
    mode: Literal["enforce", "warn", "monitor"] = "enforce"
    exempt: bool = False


class AdmissionConfig(BaseSettings):
    """Main configuration for admission controller."""
    
    # Server configuration
    bind_address: str = Field(default="127.0.0.1", env="ADMISSION_BIND_ADDRESS")
    port: int = Field(default=8443, env="ADMISSION_PORT", ge=1, le=65535)
    
    # TLS configuration
    tls_cert_path: Optional[Path] = Field(default=None, env="TLS_CERT_PATH")
    tls_key_path: Optional[Path] = Field(default=None, env="TLS_KEY_PATH")
    
    # OPA configuration
    opa_url: str = Field(default="http://localhost:8181", env="OPA_URL")
    opa_timeout: float = Field(default=5.0, env="OPA_TIMEOUT", gt=0)
    
    # Policy configuration
    policy_path: Path = Field(default="/etc/opa/policies", env="POLICY_PATH")
    
    # Registry allowlist
    allowed_registries: List[str] = Field(
        default=["docker.io", "gcr.io", "quay.io", "localhost:30500"],
        env="ALLOWED_REGISTRIES"
    )
    
    # Cache configuration
    cache_enabled: bool = Field(default=True, env="CACHE_ENABLED")
    cache_ttl: int = Field(default=300, env="CACHE_TTL", ge=0)
    
    # Enforcement configuration
    enforcement_mode: Literal["enforce", "warn", "monitor"] = Field(
        default="enforce",
        env="ENFORCEMENT_MODE"
    )
    
    # Namespace policies
    namespace_policies: Dict[str, NamespacePolicy] = Field(
        default={
            "kube-system": NamespacePolicy(mode="warn", exempt=False),
            "kube-public": NamespacePolicy(mode="warn", exempt=False),
            "kube-node-lease": NamespacePolicy(mode="warn", exempt=False),
            "gpu-operator": NamespacePolicy(mode="warn", exempt=False),
            "chutes": NamespacePolicy(mode="enforce", exempt=False),
            "default": NamespacePolicy(mode="enforce", exempt=False),
        }
    )
    
    # Debug mode
    debug: bool = Field(default=False, env="DEBUG")
    
    # Metrics configuration
    metrics_enabled: bool = Field(default=True, env="METRICS_ENABLED")
    
    # Config file support
    config_file: Optional[Path] = Field(default=None, env="CONFIG_FILE")
    
    class Config:
        """Pydantic configuration."""
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        # Allow custom parsing for complex fields
        json_encoders = {
            Path: str
        }
    
    @field_validator("allowed_registries", pre=True)
    def parse_registries(cls, v):
        """Parse comma-separated registry list from environment."""
        if isinstance(v, str):
            return [r.strip() for r in v.split(",") if r.strip()]
        return v
    
    @field_validator("namespace_policies", pre=True)
    def parse_namespace_policies(cls, v):
        """Parse namespace policies from JSON string or dict."""
        if isinstance(v, str):
            try:
                policies_dict = json.loads(v)
                return {
                    ns: NamespacePolicy(**policy) if isinstance(policy, dict) else policy
                    for ns, policy in policies_dict.items()
                }
            except json.JSONDecodeError:
                # Return default if parsing fails
                return cls.__fields__["namespace_policies"].default
        elif isinstance(v, dict):
            return {
                ns: NamespacePolicy(**policy) if isinstance(policy, dict) else policy
                for ns, policy in v.items()
            }
        return v
    
    @field_validator("tls_cert_path", "tls_key_path", "policy_path")
    def validate_paths(cls, v, field):
        """Validate that paths exist if specified."""
        if v is not None and field.name != "policy_path":
            # For optional paths, only validate if provided
            path = Path(v) if not isinstance(v, Path) else v
            if not path.exists():
                raise ValueError(f"Path does not exist: {path}")
            return path
        elif v is not None:
            # For required paths, ensure they exist or can be created
            path = Path(v) if not isinstance(v, Path) else v
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
            return path
        return v
    
    def __init__(self, **kwargs):
        """Initialize config with support for config file."""
        # First, check if config file is specified in env or kwargs
        config_file = kwargs.get("config_file") or Path(
            kwargs.get("CONFIG_FILE", "/etc/admission-controller/config.json")
        )
        
        # Load from config file if it exists
        file_config = {}
        if config_file and Path(config_file).exists():
            with open(config_file, 'r') as f:
                file_config = json.load(f)
        
        # Merge configurations (env vars take precedence over file)
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
        return self.json(indent=2, exclude_unset=False)
    
    def export_dict(self) -> dict:
        """Export configuration as dictionary."""
        return self.dict(exclude_unset=False)


# Optional: Separate configs for different components
class OPAConfig(BaseSettings):
    """Configuration specific to OPA."""
    
    opa_binary_path: Path = Field(default="/usr/local/bin/opa", env="OPA_BINARY_PATH")
    opa_log_level: Literal["debug", "info", "warn", "error"] = Field(
        default="info",
        env="OPA_LOG_LEVEL"
    )
    opa_decision_logs: bool = Field(default=False, env="OPA_DECISION_LOGS")
    opa_diagnostic_addr: str = Field(default="0.0.0.0:8282", env="OPA_DIAGNOSTIC_ADDR")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


class CosignConfig(BaseSettings):
    """Configuration for Cosign integration (Phase 4b)."""
    
    cosign_enabled: bool = Field(default=False, env="COSIGN_ENABLED")
    cosign_public_key: Optional[Path] = Field(default=None, env="COSIGN_PUBLIC_KEY")
    cosign_kms_key: Optional[str] = Field(default=None, env="COSIGN_KMS_KEY")
    cosign_keyless: bool = Field(default=False, env="COSIGN_KEYLESS")
    cosign_fulcio_url: str = Field(
        default="https://fulcio.sigstore.dev",
        env="COSIGN_FULCIO_URL"
    )
    cosign_rekor_url: str = Field(
        default="https://rekor.sigstore.dev",
        env="COSIGN_REKOR_URL"
    )
    cosign_cache_ttl: int = Field(default=3600, env="COSIGN_CACHE_TTL", ge=0)
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
    
    @field_validator("cosign_public_key")
    def validate_public_key(cls, v):
        """Validate that public key exists if specified."""
        if v is not None:
            path = Path(v) if not isinstance(v, Path) else v
            if not path.exists():
                raise ValueError(f"Cosign public key not found: {path}")
            return path
        return v


# For backward compatibility and convenience
def load_config(**kwargs) -> AdmissionConfig:
    """Load configuration with environment variables and optional overrides."""
    return AdmissionConfig(**kwargs)


# Example usage and testing
if __name__ == "__main__":
    # Load config (automatically reads from env vars)
    config = AdmissionConfig()
    
    # Print configuration
    print("=== Admission Controller Configuration ===")
    print(config.export_json())
    
    # Access configuration values
    print(f"\nServer: {config.bind_address}:{config.port}")
    print(f"OPA URL: {config.opa_url}")
    print(f"Allowed Registries: {config.allowed_registries}")
    print(f"Cache TTL: {config.cache_ttl}s")
    
    # Check namespace policies
    for ns in ["default", "kube-system", "custom"]:
        policy = config.get_namespace_policy(ns)
        print(f"\nNamespace '{ns}': mode={policy.mode}, exempt={policy.exempt}")