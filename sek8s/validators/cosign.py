import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from sek8s.validators.base import ValidatorBase, ValidationResult
from sek8s.config import AdmissionConfig, CosignConfig, CosignVerificationConfig
from sek8s.clients.cosign import CosignClient, CosignRateLimitError, CosignVerificationUnavailableError
from sek8s.image_utils import extract_registry, is_digest_pinned_reference, parse_image_reference, strip_tag

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
RateLimitError = CosignRateLimitError

# When upstream registry returns 429, pause all cosign calls for this long.
_RATE_LIMIT_BACKOFF_SECONDS = 300

# Transient errors (DNS, connection refused) are cached for a short window so we
# don't spam a broken endpoint, but retry quickly once it recovers.
_TRANSIENT_CACHE_SECONDS = 30

# Hard cap on verify cache entries to bound memory.
_CACHE_MAX_SIZE = 2048

_MAX_RECENT_TRIGGERS = 20


@dataclass
class _ImageStats:
    """Per-image Docker Hub request tracking."""

    attempts: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    is_tag_only: bool = True
    recent_triggers: List[Tuple[str, str, str]] = field(default_factory=list)

    def record_trigger(self, kind: str, name: str, namespace: str) -> None:
        if len(self.recent_triggers) < _MAX_RECENT_TRIGGERS:
            self.recent_triggers.append((kind, name, namespace))


@dataclass
class DockerHubTracker:
    """Tracks cosign verification calls that target Docker Hub."""

    docker_hub_verify_attempts: int = 0
    docker_hub_cache_hits: int = 0
    docker_hub_cache_misses: int = 0
    other_registry_verify_calls: int = 0
    rate_limit_events: int = 0
    docker_hub_images: Dict[str, _ImageStats] = field(default_factory=dict)

    def record_attempt(
        self, image: str, registry: str, cache_hit: bool,
        kind: str, name: str, namespace: str,
    ) -> None:
        is_docker_hub = registry == "docker.io"
        if is_docker_hub:
            self.docker_hub_verify_attempts += 1
            if cache_hit:
                self.docker_hub_cache_hits += 1
            else:
                self.docker_hub_cache_misses += 1

            stats = self.docker_hub_images.get(image)
            if stats is None:
                stats = _ImageStats(is_tag_only=not is_digest_pinned_reference(image))
                self.docker_hub_images[image] = stats
            stats.attempts += 1
            if cache_hit:
                stats.cache_hits += 1
            else:
                stats.cache_misses += 1
            stats.record_trigger(kind, name, namespace)
        else:
            self.other_registry_verify_calls += 1

    def record_rate_limit(self) -> None:
        self.rate_limit_events += 1

    def get_stats(self) -> dict:
        top_images = sorted(
            self.docker_hub_images.items(),
            key=lambda kv: kv[1].cache_misses,
            reverse=True,
        )
        return {
            "docker_hub_verify_attempts": self.docker_hub_verify_attempts,
            "docker_hub_cache_hits": self.docker_hub_cache_hits,
            "docker_hub_cache_misses": self.docker_hub_cache_misses,
            "other_registry_verify_calls": self.other_registry_verify_calls,
            "rate_limit_events": self.rate_limit_events,
            "docker_hub_images": [
                {
                    "image": img,
                    "tag_only": s.is_tag_only,
                    "attempts": s.attempts,
                    "cache_hits": s.cache_hits,
                    "cache_misses": s.cache_misses,
                    "recent_triggers": [
                        {"kind": k, "name": n, "namespace": ns}
                        for k, n, ns in s.recent_triggers
                    ],
                }
                for img, s in top_images
            ],
        }


@dataclass
class _CacheEntry:
    """Cached cosign outcome: digest success/failure, tag failure only, or transient error."""

    valid: Optional[bool]
    error: Optional[str]
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at


@dataclass
class _TagVerification:
    """Cached tag -> digest mapping from a successful cosign verification.

    Only populated for images on the ``pin_digest_whitelist``.  The mutating
    webhook reads this to decide whether to pin an image to a digest.
    """

    digest: str
    verified_at: float
    ttl: float

    @property
    def expired(self) -> bool:
        return time.monotonic() >= (self.verified_at + self.ttl)


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
    """Validator that verifies container image signatures using cosign.

    Caching (key = full image string):

    - **Digest-pinned** — cache success and failure with configured TTLs.
    - **Tag-only** — never cache success (tag can move). Cache **invalid** signature
      results for ``tag_failure_cache_ttl_seconds`` so kube retries do not hammer
      the registry. Set TTL to ``0`` to disable.
    - **Whitelisted tag-only** — after a successful verify, store the verified
      digest in ``_tag_cache`` with a per-image TTL.  The mutating webhook reads
      ``get_pinned_digest`` to pin pod images to that digest, and the subsequent
      validating pass hits the digest-pinned cache for zero Docker Hub calls.
    - **Transient** errors (any ref) — short TTL to avoid spamming a broken upstream.

    Upstream HTTP 429 from cosign/registry triggers a global cooldown (reactive only;
    there is no proactive admission-side RPM throttle).
    """

    def __init__(self, config: AdmissionConfig):
        super().__init__(config)
        self.cosign_config = CosignConfig()
        self._cosign_client = CosignClient()
        self._cache: Dict[str, _CacheEntry] = {}
        self._tag_cache: Dict[str, _TagVerification] = {}
        self._rate_limited_until = 0.0
        self.hub_tracker = DockerHubTracker()

    # ------------------------------------------------------------------
    # Rule sets
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Validate entry point
    # ------------------------------------------------------------------

    async def validate(self, admission_review: Dict) -> ValidationResult:
        """Validate admission request: for pod-like resources with images, require valid cosign signatures; allow otherwise."""
        request = admission_review.get("request", {})

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

        if request.get("operation") == "DELETE":
            return ValidationResult.allow()

        obj = request.get("object", {})
        images = self.extract_images(obj)
        namespace = request.get("namespace", "default")

        pod_name = obj.get("metadata", {}).get("name", "Unknown")
        logger.debug(f"Found {len(images)} images for pod {pod_name}")

        if not images:
            return ValidationResult.allow()

        ctx = ValidationContext(
            config=self.config,
            request=request,
            namespace=namespace,
            images=images,
            cosign_config=self.cosign_config,
            validator=self,
        )
        rules = self._get_rules_for_context(ctx)

        violations: List[str] = []
        for rule in rules:
            try:
                violations.extend(await rule(ctx))
            except CosignVerificationUnavailableError as e:
                logger.warning(f"Cosign verification unavailable (network/infra): {e}")
                return ValidationResult.deny(
                    f"Cosign verification unavailable (network/infra): {e}"
                )
            except RateLimitError as e:
                logger.warning(f"Rate limited: {e}")
                violations.append(str(e))
                break
            except Exception as e:
                logger.exception(f"Rule {getattr(rule, '__name__', rule)} failed")
                violations.append(f"Verification failed: {str(e)}")

        if violations:
            return ValidationResult.deny("; ".join(violations))
        return ValidationResult.allow()

    # ------------------------------------------------------------------
    # Generic rules
    # ------------------------------------------------------------------

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
        obj_meta = ctx.request.get("object", {}).get("metadata", {})
        resource_kind = ctx.request.get("kind", {}).get("kind", "Unknown")
        resource_name = obj_meta.get("name") or obj_meta.get("generateName", "unknown")

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
                is_valid = await ctx.validator._verify_image_signature(
                    image, vc,
                    resource_kind=resource_kind,
                    resource_name=resource_name,
                    namespace=ctx.namespace,
                )
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

    # ------------------------------------------------------------------
    # Core verify + cache
    # ------------------------------------------------------------------

    def _read_cache(self, image: str, digest_pinned: bool) -> Optional[bool]:
        """Return cached bool result, or None if miss. Raises on cached transient error."""
        entry = self._cache.get(image)
        if not entry or entry.expired:
            return None
        if entry.valid is None:
            raise CosignVerificationUnavailableError(entry.error or "")
        if entry.valid is False:
            logger.info(f"Cosign cache hit for {image} (cached invalid signature)")
            return False
        if digest_pinned:
            logger.info(f"Cosign cache hit for {image} (valid=True)")
            return True
        return None

    async def _verify_image_signature(
        self,
        image: str,
        verification_config: CosignVerificationConfig,
        resource_kind: str = "Unknown",
        resource_name: str = "unknown",
        namespace: str = "default",
    ) -> bool:
        """Verify image signature with registry-friendly caching (see class docstring)."""
        digest_pinned = is_digest_pinned_reference(image)
        registry = extract_registry(image)

        cached = self._read_cache(image, digest_pinned)
        if cached is not None:
            self.hub_tracker.record_attempt(
                image, registry, cache_hit=True,
                kind=resource_kind, name=resource_name, namespace=namespace,
            )
            return cached

        if self._rate_limited_until and time.monotonic() < self._rate_limited_until:
            self.hub_tracker.record_rate_limit()
            raise RateLimitError(
                "Cosign verification paused due to upstream rate limiting"
            )

        self.hub_tracker.record_attempt(
            image, registry, cache_hit=False,
            kind=resource_kind, name=resource_name, namespace=namespace,
        )
        if registry == "docker.io":
            tag_label = "TAG-ONLY" if not digest_pinned else "digest-pinned"
            logger.info(
                "DOCKER HUB HIT: cosign verify image=%s (%s, miss #%d to docker.io, "
                "triggered by %s/%s in %s)",
                image, tag_label, self.hub_tracker.docker_hub_cache_misses,
                resource_kind, resource_name, namespace,
            )

        try:
            valid, verified_digest = await self._cosign_client.verify(
                image, verification_config, timeout=60.0
            )
        except CosignVerificationUnavailableError as e:
            self._put(image, None, str(e), _TRANSIENT_CACHE_SECONDS)
            raise
        except CosignRateLimitError:
            self._rate_limited_until = time.monotonic() + _RATE_LIMIT_BACKOFF_SECONDS
            self.hub_tracker.record_rate_limit()
            raise
        except Exception as e:
            logger.error(f"cosign verify exception for {image}: {e}")
            valid = False
            verified_digest = None

        if digest_pinned:
            ttl = (
                self.cosign_config.success_cache_ttl_seconds
                if valid
                else self.cosign_config.failure_cache_ttl_seconds
            )
            self._put(image, valid, None, float(ttl))
        elif not valid:
            ttl = self.cosign_config.tag_failure_cache_ttl_seconds
            if ttl > 0:
                self._put(image, False, None, float(ttl))
        elif valid and verified_digest:
            image_no_tag = strip_tag(image)
            pin_ttl = self.cosign_config.get_pin_ttl(image_no_tag)
            if pin_ttl is not None:
                self._tag_cache[image] = _TagVerification(
                    digest=verified_digest,
                    verified_at=time.monotonic(),
                    ttl=float(pin_ttl),
                )
                digest_ref = f"{image_no_tag}@{verified_digest}"
                self._put(digest_ref, True, None, float(pin_ttl))
                logger.info(
                    "Cached tag pin: %s -> %s (TTL %ds)",
                    image, verified_digest, pin_ttl,
                )

        return valid

    def get_pinned_digest(self, image: str) -> Optional[str]:
        """Return a verified digest for a whitelisted tag-only image if within TTL.

        Called by the mutating webhook to decide whether to pin the image to a
        specific digest.  Returns ``None`` if the image is not whitelisted, has
        never been verified, or the TTL has expired.
        """
        entry = self._tag_cache.get(image)
        if entry and not entry.expired:
            return entry.digest
        return None

    def get_stats(self) -> dict:
        """Return combined Docker Hub tracking stats and cosign client call stats."""
        active_pins = {
            img: {"digest": tv.digest, "remaining_s": round(tv.ttl - (time.monotonic() - tv.verified_at), 1)}
            for img, tv in self._tag_cache.items()
            if not tv.expired
        }
        return {
            "hub_tracker": self.hub_tracker.get_stats(),
            "cosign_client": self._cosign_client.get_call_stats(),
            "cache_size": len(self._cache),
            "tag_pin_cache_size": len(active_pins),
            "active_tag_pins": active_pins,
            "rate_limited_until": (
                self._rate_limited_until - time.monotonic()
                if self._rate_limited_until and time.monotonic() < self._rate_limited_until
                else 0
            ),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _put(
        self,
        key: str,
        valid: Optional[bool],
        error: Optional[str],
        ttl: float,
    ) -> None:
        if len(self._cache) >= _CACHE_MAX_SIZE:
            now = time.monotonic()
            self._cache = {k: v for k, v in self._cache.items() if v.expires_at > now}
        self._cache[key] = _CacheEntry(
            valid=valid, error=error, expires_at=time.monotonic() + ttl
        )
