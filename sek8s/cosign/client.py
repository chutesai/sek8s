"""CosignClient: single source of truth for cosign verification.

Used by CosignValidator (admission control) and ImageManager (pull-time verify).
Keeps verification logic consistent across cosign version changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from sek8s.config import CosignVerificationConfig

logger = logging.getLogger(__name__)


class CosignRateLimitError(Exception):
    """Raised when upstream registry signals rate limiting."""


class CosignVerificationUnavailableError(Exception):
    """Raised when cosign cannot verify due to network/infra failure."""


class CosignClient:
    """Client for verifying container image signatures via cosign subprocess."""

    async def verify(
        self,
        image: str,
        config: CosignVerificationConfig,
        *,
        timeout: float = 60.0,
    ) -> bool:
        """Verify image signature. Returns True if valid."""
        if config.verification_method == "key":
            return await self._verify_with_key(image, config, timeout=timeout)
        if config.verification_method == "keyless":
            return await self._verify_keyless(image, config, timeout=timeout)
        logger.error("Unknown verification method: %s", config.verification_method)
        return False

    async def _verify_with_key(
        self,
        image: str,
        config: CosignVerificationConfig,
        *,
        timeout: float = 60.0,
    ) -> bool:
        """Verify using public key."""
        if not config.public_key or not config.public_key.exists():
            logger.error("Cosign public key not found: %s", config.public_key)
            return False

        cmd = [
            "cosign",
            "verify",
            "--key",
            str(config.public_key),
        ]
        if config.allow_http:
            cmd.append("--allow-http-registry")
        if config.allow_insecure:
            cmd.append("--allow-insecure-registry")
        if config.rekor_url:
            cmd.extend(["--rekor-url", config.rekor_url])
        cmd.append(image)

        success, _stdout, stderr = await self._run_cosign(cmd, timeout=timeout)
        if not success:
            logger.warning("Cosign verify failed for %s: %s", image, stderr)
        return success

    async def _verify_keyless(
        self,
        image: str,
        config: CosignVerificationConfig,
        *,
        timeout: float = 60.0,
    ) -> bool:
        """Verify using keyless (OIDC)."""
        if not config.keyless_identity_regex or not config.keyless_issuer:
            logger.error("Keyless verification requires identity regex and issuer")
            return False

        cmd = [
            "cosign",
            "verify",
            "--certificate-identity-regexp",
            config.keyless_identity_regex,
            "--certificate-oidc-issuer",
            config.keyless_issuer,
            image,
        ]
        if config.rekor_url:
            cmd.extend(["--rekor-url", config.rekor_url])
        if config.fulcio_url:
            cmd.extend(["--fulcio-url", config.fulcio_url])

        success, stdout, _stderr = await self._run_cosign(cmd, timeout=timeout)
        if success:
            try:
                result = json.loads(stdout)
                return isinstance(result, list) and len(result) > 0
            except json.JSONDecodeError:
                logger.error("Invalid JSON from cosign: %s", stdout)
                return False
        return False

    _RATE_LIMIT_PATTERNS = [
        re.compile(r"\brate\s*limit", re.IGNORECASE),
        re.compile(r"\b429\b"),
        re.compile(r"too many requests", re.IGNORECASE),
        re.compile(r"pull rate limit", re.IGNORECASE),
    ]
    _CONNECTION_FAILURE_INDICATORS = [
        "connection refused",
        "connection reset",
        "dial tcp",
        "i/o timeout",
        "temporary failure",
        "no such host",
        "connection timed out",
    ]

    async def _run_cosign(
        self,
        cmd: list[str],
        timeout: float = 60.0,
    ) -> tuple[bool, str, str]:
        """Run cosign command. Returns (success, stdout, stderr).
        Raises CosignRateLimitError or CosignVerificationUnavailableError when detected.
        """
        logger.debug("Running cosign: %s", " ".join(cmd))
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            combined = f"{stdout}\n{stderr}"
            if any(p.search(combined) for p in self._RATE_LIMIT_PATTERNS):
                raise CosignRateLimitError(
                    "Cosign verification rate limited by upstream registry"
                )
            if process.returncode != 0:
                combined_lower = combined.lower()
                if any(ind in combined_lower for ind in self._CONNECTION_FAILURE_INDICATORS):
                    raise CosignVerificationUnavailableError(
                        stderr or stdout or "Registry/network unavailable"
                    )
            return (process.returncode == 0, stdout, stderr)
        except (CosignRateLimitError, CosignVerificationUnavailableError):
            raise
        except asyncio.TimeoutError:
            logger.error("Cosign timeout after %.0fs", timeout)
            return (False, "", "timeout")
        except FileNotFoundError:
            logger.error("Cosign binary not found")
            return (False, "", "cosign not found")
        except Exception as e:
            logger.exception("Cosign error: %s", e)
            return (False, "", str(e))
