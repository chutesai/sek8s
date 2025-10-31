import asyncio
import json
import logging
import re
from typing import Dict, Optional
from urllib.parse import urlparse

from sek8s.validators.base import ValidatorBase, ValidationResult
from sek8s.config import AdmissionConfig, CosignConfig, CosignRegistryConfig


logger = logging.getLogger(__name__)


class CosignValidator(ValidatorBase):
    """Validator that verifies container image signatures using cosign."""

    def __init__(self, config: AdmissionConfig):
        super().__init__(config)
        self.cosign_config = CosignConfig()

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

        if not images:
            return ValidationResult.allow()

        # Check each image
        violations = []
        for image in images:
            try:
                # Extract registry from image
                registry = self._extract_registry(image)

                # Get registry-specific cosign configuration
                registry_config = self.cosign_config.get_registry_config(registry)

                if not registry_config:
                    logger.warning(
                        f"No cosign configuration found for registry {registry}, skipping verification"
                    )
                    continue

                # Skip verification if disabled for this registry
                if (
                    registry_config.verification_method == "disabled"
                    or not registry_config.require_signature
                ):
                    logger.debug(f"Signature verification disabled for registry {registry}")
                    continue

                # Verify the image signature based on configuration
                is_valid = await self._verify_image_signature(image, registry_config)
                if not is_valid:
                    violations.append(
                        f"Image {image} has invalid or missing signature (registry: {registry})"
                    )

            except Exception as e:
                logger.error(f"Error verifying image {image}: {e}")
                violations.append(f"Verification failed for {image}: {str(e)}")

        if violations:
            return ValidationResult.deny("; ".join(violations))
        else:
            return ValidationResult.allow()

    def _extract_registry(self, image: str) -> str:
        """Extract registry hostname from image reference."""
        # Handle different image reference formats:
        # - docker.io/library/nginx:latest
        # - gcr.io/project/image:tag
        # - localhost:5000/myimage
        # - nginx (implies docker.io)

        # First, split off digest if present
        if "@" in image:
            image = image.split("@")[0]

        # If no slash, it's an official Docker Hub image (e.g., "nginx")
        if "/" not in image:
            return "docker.io"

        # Split into parts
        parts = image.split("/")
        first_part = parts[0]

        # If first part contains dot or colon, it's likely a registry
        # This handles: gcr.io/project/image, localhost:5000/image
        if "." in first_part or ":" in first_part:
            return first_part

        # Special case for Docker Hub images (e.g., "library/nginx")
        if len(parts) == 2 and first_part in ["library"]:
            return "docker.io"

        # If first part is a username/org without registry, assume Docker Hub
        # This handles: username/repo, org/repo
        if len(parts) == 2:
            return "docker.io"

        # Otherwise, first part is the registry
        return first_part

    async def _verify_image_signature(
        self, image: str, registry_config: CosignRegistryConfig
    ) -> bool:
        """Verify image signature using cosign based on registry configuration."""
        try:
            # Resolve tag to digest if needed for consistent signature verification
            resolved_image = await self._resolve_image_reference(image)

            if registry_config.verification_method == "key":
                return await self._verify_with_key(resolved_image, registry_config)
            elif registry_config.verification_method == "keyless":
                return await self._verify_keyless(resolved_image, registry_config)
            else:
                logger.error(f"Unknown verification method: {registry_config.verification_method}")
                return False

        except Exception as e:
            logger.error(f"Exception during cosign verification: {e}")
            return False

    async def _verify_with_key(self, image: str, registry_config: CosignRegistryConfig) -> bool:
        """Verify image signature using a public key."""
        if not registry_config.public_key or not registry_config.public_key.exists():
            logger.error(f"Public key not found: {registry_config.public_key}")
            return False

        try:
            # Build cosign verify command
            cmd = ["cosign", "verify", "--key", str(registry_config.public_key), image]

            # Add Rekor URL if specified
            if registry_config.rekor_url:
                cmd.extend(["--rekor-url", registry_config.rekor_url])

            logger.debug(f"Running: {' '.join(cmd)}")

            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                # Additional validation: ensure output is valid JSON
                try:
                    verification_result = json.loads(stdout.decode())
                    # Cosign verify returns a list of verification results
                    return isinstance(verification_result, list) and len(verification_result) > 0
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON output from cosign verify: {stdout.decode()}")
                    return False
            else:
                logger.debug(f"Cosign key verification failed for {image}: {stderr.decode()}")
                return False

        except Exception as e:
            logger.error(f"Exception during key-based verification: {e}")
            return False

    async def _verify_keyless(self, image: str, registry_config: CosignRegistryConfig) -> bool:
        """Verify image signature using keyless verification (OIDC)."""
        if not registry_config.keyless_identity_regex or not registry_config.keyless_issuer:
            logger.error("Keyless verification requires identity regex and issuer")
            return False

        try:
            # Build cosign verify command for keyless
            cmd = [
                "cosign",
                "verify",
                "--certificate-identity-regexp",
                registry_config.keyless_identity_regex,
                "--certificate-oidc-issuer",
                registry_config.keyless_issuer,
                image,
            ]

            # Add URLs if specified
            if registry_config.rekor_url:
                cmd.extend(["--rekor-url", registry_config.rekor_url])
            if registry_config.fulcio_url:
                cmd.extend(["--fulcio-url", registry_config.fulcio_url])

            logger.debug(f"Running: {' '.join(cmd)}")

            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                # Additional validation: ensure output is valid JSON
                try:
                    verification_result = json.loads(stdout.decode())
                    # Cosign verify returns a list of verification results
                    return isinstance(verification_result, list) and len(verification_result) > 0
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON output from cosign verify: {stdout.decode()}")
                    return False
            else:
                logger.debug(f"Cosign keyless verification failed for {image}: {stderr.decode()}")
                return False

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
