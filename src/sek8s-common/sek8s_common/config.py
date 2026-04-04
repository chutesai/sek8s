"""Shared configuration base classes for sek8s services."""

from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseSettings):
    bind_address: str = Field(default="127.0.0.1")
    port: int = Field(default=8443, ge=1, le=65535)
    uds_path: Optional[Path] = Field(default=None)
    require_tls: bool = Field(default=True, alias="REQUIRE_TLS")

    tls_cert_path: Optional[Path] = Field(default=None, alias="TLS_CERT_PATH")
    tls_key_path: Optional[Path] = Field(default=None, alias="TLS_KEY_PATH")
    client_ca_path: Optional[Path] = Field(default=None, alias="CLIENT_CA_PATH")
    mtls_required: bool = Field(default=False, alias="MTLS_REQUIRED")

    debug: bool = Field(default=False, alias="DEBUG")

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", case_sensitive=False, env_prefix="", extra="ignore"
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
    allowed_validators_str: Optional[str] = Field(
        default=None, alias="ALLOWED_VALIDATORS"
    )

    _allowed_validators: Optional[list[str]] = None

    @property
    def allowed_validators(self) -> list[str]:
        """Parse comma-separated validator list."""
        if self._allowed_validators is None:
            if self.allowed_validators_str:
                self._allowed_validators = [
                    item.strip()
                    for item in self.allowed_validators_str.split(",")
                    if item.strip()
                ]
            else:
                self._allowed_validators = []
        return self._allowed_validators

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )
