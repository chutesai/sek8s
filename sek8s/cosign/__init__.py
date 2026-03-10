"""Cosign integration: signature verification for container images."""

from .client import (
    CosignClient,
    CosignRateLimitError,
    CosignVerificationUnavailableError,
)

__all__ = [
    "CosignClient",
    "CosignRateLimitError",
    "CosignVerificationUnavailableError",
]
