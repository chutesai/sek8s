import asyncio
import json
import logging
import re
from typing import Dict, Optional
from urllib.parse import urlparse

from sek8s.validators.base import ValidatorBase, ValidationResult
from sek8s.config import AdmissionConfig, CosignConfig, CosignRegistryConfig, CosignVerificationConfig


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

        logger.debug(f"Found {len(images)} images for pod {obj.get('metadata', {}).get('name', 'Unknown')}")

        if not images:
            return ValidationResult.allow()

        # Check each image
        violations = []
        for image in images:
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
        try:
            logger.debug(f"Verifying image signature for {image=}")
            # Resolve tag to digest if needed for consistent signature verification
            resolved_image = await self._resolve_image_reference(image)

            if verification_config.verification_method == "key":
                return await self._verify_with_key(resolved_image, verification_config)
            elif verification_config.verification_method == "keyless":
                return await self._verify_keyless(resolved_image, verification_config)
            else:
                logger.error(f"Unknown verification method: {verification_config.verification_method}")
                return False

        except Exception as e:
            logger.error(f"Exception during cosign verification: {e}")
            return False

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

                logger.debug(f"Running: {' '.join(cmd)}")

                process = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                
                await process.wait()

                if process.returncode == 0:
                    result_output = await process.stdout.read()
                    try:
                        verification_result = json.loads(result_output.decode())
                        logger.debug(f"Verification result: {verification_result}")
                        valid = True
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON output from cosign verify: {result_output.decode()}")
                else:
                    result_output = await process.stderr.read()
                    logger.error(f"Cosign key verification failed for {image}: {result_output.decode()}")
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

            logger.debug(f"Running: {' '.join(cmd)}")

            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                try:
                    verification_result = json.loads(stdout.decode())
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
