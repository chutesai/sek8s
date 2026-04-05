"""Configuration for attestation proxy service."""

from pydantic import Field
from pydantic_settings import SettingsConfigDict
from sek8s_common.config import AuthConfig


class AttestationProxyConfig(AuthConfig):
    """Configuration for attestation proxy service.

    Requires auth fields to be configured.
    """

    allowed_validators_str: str = Field(..., alias="ALLOWED_VALIDATORS")
    miner_ss58: str = Field(..., alias="MINER_SS58")

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )
