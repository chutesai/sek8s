import asyncio
import json
import logging
import re
import time
from typing import Dict, Tuple
from urllib.parse import urlparse

from cachetools import TTLCache

from sek8s.validators.base import ValidatorBase, ValidationResult
from sek8s.config import AdmissionConfig, CosignConfig, CosignRegistryConfig, CosignVerificationConfig


logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    """Raised when upstream registry signals rate limiting."""


class CosignValidator(ValidatorBase):
    """Validator that verifies container image signatures using cosign."""

    def __init__(self, config: AdmissionConfig):
        super().__init__(config)
        self.cosign_config = CosignConfig()
        self._result_cache = TTLCache(
            maxsize=self.cosign_config.cache_maxsize, ttl=self.cosign_config.cache_ttl
        )
        self._negative_cache = TTLCache(
            maxsize=self.cosign_config.cache_maxsize, ttl=self.cosign_config.negative_cache_ttl
        )
        self._rate_limit_until = 0.0
        self._rate_limit_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in [
                r"\brate\s*limit",
                r"\b429\b",
                r"too many requests",
                r"pull rate limit",
            ]
        ]

    async def validate(self, admission_review: Dict) -> ValidationResult:
        """Validate that all container images have valid cosign signatures."""
        request = admission_review.get("request", {})

        # Only check pods and pod-creating resources
        kind = request.get("kind", {}).get("kind", "")
        if kind not in [
            "Pod",
            "Deployment",
            "StatefulSet",
            "DaemonSet",
            "Job",
            "CronJob",
            "ReplicaSet",
        ]:
            return ValidationResult.allow()

        operation = request.get("operation", None)
        if operation == "DELETE":
            return ValidationResult.allow()

        # Extract images
        obj = request.get("object", {})
        images = self.extract_images(obj)

        logger.debug(f"Found {len(images)} images for pod {obj.get('metadata', {}).get('name', 'Unknown')}")

        if not images:
            return ValidationResult.allow()

        # Check each image
        violations = []
        seen = set()
        for image in images:
            if image in seen:
                continue
            seen.add(image)
            try:
                # Parse image reference into components
                registry, org, repo, tag = self._parse_image_reference(image)
                
                logger.debug(f"Parsed image {image} -> registry={registry}, org={org}, repo={repo}, tag={tag}")

                # Get the most specific cosign configuration
                verification_config = self.cosign_config.get_verification_config(registry, org, repo)

                if not verification_config:
                    logger.warning(
                        f"No cosign configuration found for {registry}/{org}/{repo}, skipping verification"
                    )
                    continue

                # Skip verification if disabled
                if (
                    verification_config.verification_method == "disabled"
                    or not verification_config.require_signature
                ):
                    logger.debug(f"Signature verification disabled for {registry}/{org}/{repo}")
                    continue

                # Verify the image signature
                is_valid = await self._verify_image_signature(image, verification_config)
                if not is_valid:
                    violations.append(
                        f"Image {image} has invalid or missing signature (registry: {registry}, org: {org})"
                    )

            except RateLimitError as e:
                logger.warning(f"Rate limited while verifying {image}: {e}")
                violations.append(str(e))
                break  # Avoid hammering upstream during a known backoff window
            except Exception as e:
                logger.error(f"Error verifying image {image}: {e}")
                violations.append(f"Verification failed for {image}: {str(e)}")

        if violations:
            return ValidationResult.deny("; ".join(violations))
        else:
            return ValidationResult.allow()

    def _parse_image_reference(self, image: str) -> tuple[str, str, str, str]:
        """
        Parse image reference into (registry, organization, repository, tag/digest).
        
        Examples:
            nginx:latest -> (docker.io, library, nginx, latest)
            parachutes/chutes-agent:k3s -> (docker.io, parachutes, chutes-agent, k3s)
            gcr.io/distroless/base:latest -> (gcr.io, distroless, base, latest)
            gcr.io/my-project/subdir/app:v1 -> (gcr.io, my-project, subdir/app, v1)
            registry.k8s.io/pause:3.9 -> (registry.k8s.io, library, pause, 3.9)
        """
        original_image = image
        
        # Handle digest vs tag
        if "@" in image:
            image, digest = image.split("@", 1)
            tag_or_digest = f"@{digest}"
        elif ":" in image.split("/")[-1]:  # Only check last component for tag
            image, tag = image.rsplit(":", 1)
            tag_or_digest = tag
        else:
            tag_or_digest = "latest"
        
        # No slashes = official Docker Hub image (nginx, alpine, etc.)
        if "/" not in image:
            return ("docker.io", "library", image, tag_or_digest)
        
        parts = image.split("/")
        first_part = parts[0]
        
        # Check if first part is a registry (contains . or :)
        if "." in first_part or ":" in first_part:
            # Has explicit registry
            registry = first_part
            remaining = parts[1:]
            
            if len(remaining) == 0:
                raise ValueError(f"Invalid image reference: {original_image}")
            elif len(remaining) == 1:
                # registry.io/image -> assume "library" org
                org = "library"
                repo = remaining[0]
            else:
                # registry.io/org/repo or registry.io/org/subdir/repo
                org = remaining[0]
                repo = "/".join(remaining[1:])
        else:
            # No explicit registry, assume Docker Hub
            registry = "docker.io"
            
            if len(parts) == 1:
                # Should have been caught by "/" check, but just in case
                org = "library"
                repo = parts[0]
            else:
                # user/repo or user/subdir/repo
                org = parts[0]
                repo = "/".join(parts[1:])
        
        return (registry, org, repo, tag_or_digest)

    async def _verify_image_signature(
        self, image: str, verification_config: CosignVerificationConfig
    ) -> bool:
        """Verify image signature using cosign based on verification configuration."""
        logger.debug(f"Verifying image signature for {image=}")

        if self._rate_limit_until and time.time() < self._rate_limit_until:
            raise RateLimitError(
                f"Cosign verification paused due to upstream rate limiting; retry after "
                f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._rate_limit_until))}"
            )

        resolved_image = await self._resolve_image_reference(image)
        cache_key = self._make_cache_key(resolved_image, verification_config)

        if cache_key in self._result_cache:
            logger.debug(f"Cosign cache hit (positive) for {resolved_image}")
            return True
        if cache_key in self._negative_cache:
            logger.debug(f"Cosign cache hit (negative) for {resolved_image}")
            return False

        try:
            if verification_config.verification_method == "key":
                valid = await self._verify_with_key(resolved_image, verification_config)
            elif verification_config.verification_method == "keyless":
                valid = await self._verify_keyless(resolved_image, verification_config)
            else:
                logger.error(f"Unknown verification method: {verification_config.verification_method}")
                valid = False
        except RateLimitError:
            # propagate so caller can stop hammering upstream
            raise
        except Exception as e:
            logger.error(f"Exception during cosign verification: {e}")
            valid = False

        # Cache result (success in main cache; failure in short negative cache)
        if valid:
            self._result_cache[cache_key] = True
        else:
            self._negative_cache[cache_key] = False

        return valid

    async def _verify_with_key(self, image: str, verification_config: CosignVerificationConfig) -> bool:
        """Verify image signature using a public key."""
        valid = False
        if not verification_config.public_key or not verification_config.public_key.exists():
            logger.error(f"Public key not found: {verification_config.public_key}")
        else:
            try:
                cmd = [
                    "cosign", 
                    "verify",
                    "--key", 
                    str(verification_config.public_key)
                ]

                if verification_config.allow_http:
                    cmd.append("--allow-http-registry")

                if verification_config.allow_insecure:
                    cmd.append("--allow-insecure-registry")

                if verification_config.rekor_url:
                    cmd.extend(["--rekor-url", verification_config.rekor_url])

                cmd.append(image)

                success, stdout, stderr, rate_limited = await self._run_cosign(cmd)
                if rate_limited:
                    self._record_rate_limit()
                    raise RateLimitError(self._rate_limit_message())

                if success:
                    try:
                        verification_result = json.loads(stdout)
                        logger.debug(f"Verification result: {verification_result}")
                        valid = True
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON output from cosign verify: {stdout}")
                else:
                    logger.error(f"Cosign key verification failed for {image}: {stderr or stdout}")
            except RateLimitError:
                raise
            except Exception as e:
                logger.error(f"Exception during key-based verification: {e}")

        return valid

    async def _verify_keyless(self, image: str, verification_config: CosignVerificationConfig) -> bool:
        """Verify image signature using keyless verification (OIDC)."""
        if not verification_config.keyless_identity_regex or not verification_config.keyless_issuer:
            logger.error("Keyless verification requires identity regex and issuer")
            return False

        try:
            cmd = [
                "cosign",
                "verify",
                "--certificate-identity-regexp",
                verification_config.keyless_identity_regex,
                "--certificate-oidc-issuer",
                verification_config.keyless_issuer,
                image,
            ]

            if verification_config.rekor_url:
                cmd.extend(["--rekor-url", verification_config.rekor_url])
            if verification_config.fulcio_url:
                cmd.extend(["--fulcio-url", verification_config.fulcio_url])

            success, stdout, stderr, rate_limited = await self._run_cosign(cmd)
            if rate_limited:
                self._record_rate_limit()
                raise RateLimitError(self._rate_limit_message())

            if success:
                try:
                    verification_result = json.loads(stdout)
                    return isinstance(verification_result, list) and len(verification_result) > 0
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON output from cosign verify: {stdout}")
                    return False
            else:
                logger.debug(f"Cosign keyless verification failed for {image}: {stderr or stdout}")
                return False

        except RateLimitError:
            raise
        except Exception as e:
            logger.error(f"Exception during keyless verification: {e}")
            return False

    async def _resolve_image_reference(self, image: str) -> str:
        """Resolve image tag to digest if necessary."""
        # If image already has digest, return as-is
        if "@" in image:
            return image

        try:
            # Use docker inspect to resolve tag to digest
            process = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "--format={{index .RepoDigests 0}}",
                image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                digest_ref = stdout.decode().strip()
                if digest_ref and digest_ref != "<no value>":
                    logger.debug(f"Resolved {image} to {digest_ref}")
                    return digest_ref

            # If resolution fails, return original image reference
            # This allows cosign to handle the resolution
            logger.debug(f"Could not resolve {image} to digest, using original reference")
            return image

        except Exception as e:
            logger.debug(f"Could not resolve image reference {image}: {e}")
            return image

    def _normalize_registry_name(self, registry: str) -> str:
        """Normalize registry name for consistent matching."""
        # Remove protocol if present
        if registry.startswith(("http://", "https://")):
            registry = urlparse(registry).netloc

        # Handle Docker Hub special cases
        if registry in ["docker.io", "registry-1.docker.io", "index.docker.io"]:
            return "docker.io"

        return registry.lower()

    async def _run_cosign(self, cmd: list[str]) -> Tuple[bool, str, str, bool]:
        """Run cosign command and detect rate limiting."""
        logger.debug(f"Running: {' '.join(cmd)}")
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout = stdout_bytes.decode()
        stderr = stderr_bytes.decode()
        rate_limited = self._is_rate_limited(stdout, stderr)
        return process.returncode == 0, stdout, stderr, rate_limited

    def _is_rate_limited(self, stdout: str, stderr: str) -> bool:
        """Check cosign output for rate limit signals."""
        combined = f"{stdout}\n{stderr}"
        return any(p.search(combined) for p in self._rate_limit_patterns)

    def _record_rate_limit(self):
        """Back off for a configured period after a rate-limit signal."""
        self._rate_limit_until = time.time() + self.cosign_config.rate_limit_backoff_seconds

    def _rate_limit_message(self) -> str:
        """Human-friendly rate limit message."""
        if not self._rate_limit_until:
            return "Cosign verification rate limited by upstream registry"
        return (
            "Cosign verification rate limited by upstream registry; retry after "
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._rate_limit_until))}"
        )

    def _make_cache_key(
        self, resolved_image: str, verification_config: CosignVerificationConfig
    ) -> tuple:
        """Create a cache key that accounts for image digest and verification config."""
        return (
            resolved_image,
            verification_config.verification_method,
            str(verification_config.public_key) if verification_config.public_key else None,
            verification_config.keyless_identity_regex,
            verification_config.keyless_issuer,
            verification_config.rekor_url,
            verification_config.fulcio_url,
            verification_config.allow_http,
            verification_config.allow_insecure,
        )
