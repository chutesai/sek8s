import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from cachetools import TTLCache

from sek8s.validators.base import ValidatorBase, ValidationResult
from sek8s.config import AdmissionConfig, CosignConfig, CosignRegistryConfig, CosignVerificationConfig
from sek8s.cosign.client import CosignClient, CosignRateLimitError, CosignVerificationUnavailableError
from sek8s.image_utils import parse_image_reference

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
RateLimitError = CosignRateLimitError


@dataclass
class ValidationContext:
    """Context passed to validation rules: config, request, and pre-extracted data.

    required_key_path is set in _get_rules_for_context when the rule set needs it
    (e.g. chutes namespace). Rules are generic and only read context; they are
    not aware of namespace or rule-set identity.
    """

    config: AdmissionConfig
    request: dict
    namespace: str
    images: List[str]
    cosign_config: CosignConfig
    validator: "CosignValidator"
    required_key_path: Optional[Path] = None


# Rule type: async (validator, ctx) -> list of violation strings (empty if none)
Rule = Callable[["CosignValidator", ValidationContext], Awaitable[List[str]]]


class CosignValidator(ValidatorBase):
    """Validator that verifies container image signatures using cosign."""

    def __init__(self, config: AdmissionConfig):
        super().__init__(config)
        self.cosign_config = CosignConfig()
        self._cosign_client = CosignClient()
        self._result_cache = TTLCache(
            maxsize=self.cosign_config.cache_maxsize, ttl=self.cosign_config.cache_ttl
        )
        self._negative_cache = TTLCache(
            maxsize=self.cosign_config.cache_maxsize, ttl=self.cosign_config.negative_cache_ttl
        )
        self._admission_result_cache = TTLCache(
            maxsize=self.cosign_config.admission_result_cache_maxsize,
            ttl=self.cosign_config.admission_result_cache_ttl,
        )
        self._admission_cache_lock = asyncio.Lock()
        self._rate_limit_until = 0.0

    # Rule sets: properties returning lists of generic rules (class methods)
    @property
    def _chutes_rules(self) -> List[Rule]:
        """Rule set for chutes namespace: require config, key, and verify."""
        return [
            self._require_cosign_config,
            self._reject_disabled,
            self._require_key_verification,
            self._require_ctx_key,
            self._verify_cosign_config,
        ]

    @property
    def _default_rules(self) -> List[Rule]:
        """Rule set for other namespaces: verify when config exists and not disabled."""
        return [self._verify_cosign_config]

    def _get_rules_for_context(self, ctx: ValidationContext) -> List[Rule]:
        """Return the rule set to run for the given validation context.

        Builds the union of rule sets for the context and deduplicates so rule sets
        can overlap without running the same rule twice. Order of rules does not
        affect the outcome (allow/deny or which violations are found), only the
        order of messages in the denial string.
        """
        rules: set = set()
        if ctx.namespace == "chutes":
            ctx.required_key_path = self.config.chutes_cosign_public_key_path
            rules.update(self._chutes_rules)

        rules.update(self._default_rules)

        return list(rules)

    def _admission_cache_key(self, request: dict, images: List[str]) -> tuple:
        """Build a cache key for admission result so all pods with same images reuse result.

        Key is (namespace, kind, image_set) only—no name or UID. So when many new pods
        are created with the same bad image (e.g. controller replacing crashlooping pods),
        only the first admission runs cosign; the rest get a cache hit and avoid registry
        rate limits. Result is valid for admission_result_cache_ttl (default 20 min).
        """
        namespace = request.get("namespace", "default")
        kind = request.get("kind", {}).get("kind", "")
        return (namespace, kind, tuple(sorted(images)))

    async def validate(self, admission_review: Dict) -> ValidationResult:
        """Validate admission request: for pod-like resources with images, require valid cosign signatures; allow otherwise."""
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

        obj = request.get("object", {})
        images = self.extract_images(obj)
        namespace = request.get("namespace", "default")

        logger.debug(f"Found {len(images)} images for pod {obj.get('metadata', {}).get('name', 'Unknown')}")

        if not images:
            return ValidationResult.allow()

        # Admission-level cache: same pod/spec (same uid or same name+images) → return cached result to avoid repeated cosign calls
        cache_key = self._admission_cache_key(request, images)
        async with self._admission_cache_lock:
            if cache_key in self._admission_result_cache:
                cached = self._admission_result_cache[cache_key]
                logger.debug(
                    "Cosign admission cache hit for %s/%s (%s), allowed=%s",
                    namespace,
                    obj.get("metadata", {}).get("name", ""),
                    kind,
                    cached.allowed,
                )
                return cached

        # 1. Create validation context (required_key_path set in _get_rules_for_context when chutes)
        ctx = ValidationContext(
            config=self.config,
            request=request,
            namespace=namespace,
            images=images,
            cosign_config=self.cosign_config,
            validator=self,
        )
        # 2. Get validation rules (based on context)
        rules = self._get_rules_for_context(ctx)
        # 3. Run rule set
        violations: List[str] = []
        for rule in rules:
            try:
                violations.extend(await rule(ctx))
            except CosignVerificationUnavailableError as e:
                logger.warning("Cosign verification unavailable (network/infra), not caching: %s", e)
                return ValidationResult.deny(
                    f"Cosign verification unavailable (network/infra): {e}"
                )
            except RateLimitError as e:
                logger.warning(f"Rate limited: {e}")
                violations.append(str(e))
                break
            except Exception as e:
                logger.exception("Rule %s failed", getattr(rule, "__name__", rule))
                violations.append(f"Verification failed: {str(e)}")
        # 4. Return validation result and cache it for this pod/spec
        if violations:
            result = ValidationResult.deny("; ".join(violations))
        else:
            result = ValidationResult.allow()
        async with self._admission_cache_lock:
            self._admission_result_cache[cache_key] = result
        return result

    # -------------------------------------------------------------------------
    # Generic rules: operate only on context; no namespace or rule-set awareness
    # -------------------------------------------------------------------------

    async def _require_cosign_config(self, ctx: ValidationContext) -> List[str]:
        """Report any image that has no cosign configuration (used in rule sets that require config for all images)."""
        violations: List[str] = []
        seen: set = set()
        for image in ctx.images:
            if image in seen:
                continue
            seen.add(image)
            registry, org, repo, _ = parse_image_reference(image)
            vc = ctx.cosign_config.get_verification_config(registry, org, repo)
            if not vc:
                violations.append(f"Image {image} has no cosign configuration")
        return violations

    async def _reject_disabled(self, ctx: ValidationContext) -> List[str]:
        """Report any image that has verification disabled (used in rule sets that require verification)."""
        violations: List[str] = []
        seen: set = set()
        for image in ctx.images:
            if image in seen:
                continue
            seen.add(image)
            registry, org, repo, _ = parse_image_reference(image)
            vc = ctx.cosign_config.get_verification_config(registry, org, repo)
            if vc and (
                vc.verification_method == "disabled" or not vc.require_signature
            ):
                violations.append(f"Image {image} has verification disabled")
        return violations

    async def _require_key_verification(self, ctx: ValidationContext) -> List[str]:
        """Report any image not using key-based verification (used in rule sets that require a key)."""
        violations: List[str] = []
        seen: set = set()
        for image in ctx.images:
            if image in seen:
                continue
            seen.add(image)
            registry, org, repo, _ = parse_image_reference(image)
            vc = ctx.cosign_config.get_verification_config(registry, org, repo)
            if vc and (
                vc.verification_method != "key" or vc.public_key is None
            ):
                violations.append(f"Image {image} must use key-based verification")
        return violations

    async def _require_ctx_key(self, ctx: ValidationContext) -> List[str]:
        """Report any image whose cosign key path does not match ctx.required_key_path. Raises if required_key_path is not set."""
        if not ctx.required_key_path:
            raise RuntimeError(
                f"You can not use the require context key rule without providing a key path.\n"
                f"{ctx.namespace=} {ctx.required_key_path=} {ctx.images=}"
            )
        violations: List[str] = []
        seen: set = set()
        for image in ctx.images:
            if image in seen:
                continue
            seen.add(image)
            registry, org, repo, _ = parse_image_reference(image)
            vc = ctx.cosign_config.get_verification_config(registry, org, repo)
            if vc and vc.public_key is not None and str(vc.public_key) != str(ctx.required_key_path):
                violations.append(f"Image {image} uses a different cosign key")
        return violations

    async def _verify_cosign_config(self, ctx: ValidationContext) -> List[str]:
        """Verify signatures for images that have verification config enabled; skip images with no config or verification disabled."""
        violations: List[str] = []
        seen: set = set()
        for image in ctx.images:
            if image in seen:
                continue
            seen.add(image)
            registry, org, repo, _ = parse_image_reference(image)
            logger.debug(f"Parsed image {image} -> registry={registry}, org={org}, repo={repo}")
            vc = ctx.cosign_config.get_verification_config(registry, org, repo)
            if not vc:
                logger.warning(
                    f"No cosign configuration found for {registry}/{org}/{repo}, skipping verification"
                )
                continue
            if vc.verification_method == "disabled" or not vc.require_signature:
                logger.debug(f"Signature verification disabled for {registry}/{org}/{repo}")
                continue
            try:
                is_valid = await ctx.validator._verify_image_signature(image, vc)
                if not is_valid:
                    violations.append(
                        f"Image {image} has invalid or missing signature (registry: {registry}, org: {org})"
                    )
            except CosignVerificationUnavailableError:
                raise
            except RateLimitError:
                raise
            except Exception as e:
                logger.error(f"Error verifying image {image}: {e}")
                violations.append(f"Verification failed for {image}: {str(e)}")
        return violations

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
            valid = await self._cosign_client.verify(
                resolved_image, verification_config, timeout=60.0
            )
        except CosignVerificationUnavailableError:
            raise
        except CosignRateLimitError:
            self._record_rate_limit()
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

    async def _resolve_image_reference(self, image: str) -> str:
        """Resolve image reference to digest if possible; return as-is if already a digest or if resolution fails."""
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

    def _record_rate_limit(self):
        """Record rate-limit and set backoff until time so verification is paused for the configured period."""
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
        """Create a cache key from the resolved image reference and verification config."""
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
