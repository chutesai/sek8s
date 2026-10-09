"""Configuration for attestation proxy service."""

from typing import Optional

from pydantic import Field
from pydantic_settings import SettingsConfigDict
from sek8s_common.config import AuthConfig


class AttestationProxyConfig(AuthConfig):
    """Configuration for attestation proxy service.

    Requires auth fields to be configured.
    """

    allowed_validators_str: str = Field(..., alias="ALLOWED_VALIDATORS")
    miner_ss58: str = Field(..., alias="MINER_SS58")
    # Optional: the miner hotkey credential, the sr25519 private key or the seed (the
    # miner-credentials secret holds one). When set, the external proxy signs each response with
    # the miner hotkey as a release-candidate proof-of-possession. Absent (old charts running the
    # latest image) -> no signing, unchanged pass-through, so older VMs keep working.
    miner_private_key: Optional[str] = Field(default=None, alias="MINER_PRIVATE_KEY")
    miner_seed: Optional[str] = Field(default=None, alias="MINER_SEED")

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )
