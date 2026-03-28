# Feature Spec: Docker Hub Digest Pinning via Mutating Webhook

**Date**: 2026-03-25  
**Status**: in progress

---

## Context

Docker Hub rate limiting during k3s startup and steady-state operation. The primary offender is `parachutes/failed-chute-cleanup`, a CronJob that runs every minute. Each admission triggers a full `cosign verify` against Docker Hub because tag-only image references cannot be safely cached -- the tag could point to a different image at any time (TOCTOU). This alone generates ~1440 Docker Hub API calls/day, consistently exceeding rate limits.

- **Packages affected**: `sek8s.services`, `sek8s.validators`, `sek8s.clients`, `sek8s.config`
- **Key files**: `sek8s/services/admission_controller.py`, `sek8s/services/admission_models.py`, `sek8s/validators/cosign.py`, `sek8s/clients/cosign.py`, `sek8s/config.py`, `sek8s/image_utils.py`
- **Dependencies**: cosign CLI, Ansible roles for admission controller deployment

---

## Design Decisions

- **Always verify against Docker Hub** -- cosign verification is never skipped or replaced with a local shortcut. The TTL cache avoids *repeating* a verification that already succeeded, not bypassing it.
- **Whitelist-only pinning** -- the mutating webhook only pins digests for images explicitly listed in `pin_digest_whitelist` in `cosign-registries.json`. All other images pass through untouched with standard validating-webhook-only behavior. No wildcards or glob patterns -- each entry is an exact match on the fully-qualified image name (without tag) to prevent accidental broad pinning.
- **TTL-based cache expiry for update propagation** -- pinned digests expire after a configurable TTL. When expired, the tag flows through the mutating webhook unmutated, kubelet pulls the latest image from Docker Hub, and cosign verifies it fresh. This ensures image updates propagate within at most one TTL window.
- **Mutating webhook is not a security gate** -- `failurePolicy: Ignore` on the `MutatingWebhookConfiguration`. If mutation fails for any reason, the tag passes through and the validating webhook (which remains `failurePolicy: Fail`) handles it with a fresh cosign verify. Security is never weakened.
- **Digest extracted from cosign output** -- the verified digest comes from cosign's own JSON stdout (`critical.image.docker-manifest-digest`), not from containerd or any local image store. No containerd socket access or systemd hardening changes required.
- **Typed response models** -- `AdmissionReviewResponse`, `AdmissionResponseBody`, and `AdmissionStatus` Pydantic models replace raw dict construction for all admission webhook responses (both validating and mutating).

---

## API Changes

- **New endpoints**: `POST /mutate` -- mutating admission webhook handler (endpoint existed as a placeholder; now implemented with real JSON Patch logic)
- **Schema changes**: `cosign-registries.json` gains a new top-level `pin_digest_whitelist` array. `CosignConfig` gains `tag_pin_default_ttl_seconds`, `digest_pin_whitelist`, and `get_pin_ttl()`. `CosignClient.verify()` return type changed from `bool` to `tuple[bool, Optional[str]]`.
- **Migrations**: None. New config fields have sensible defaults (empty whitelist, 3600s default TTL). Existing deployments continue to work without changes to `cosign-registries.json`.

---

## Goal

Success = Docker Hub API calls for whitelisted high-frequency tag-only images are reduced by >95% while maintaining:

1. Cosign signature verification is never bypassed -- every image is verified against Docker Hub at least once per TTL window.
2. During the TTL window, kubelet pulls the exact digest that cosign verified (no TOCTOU).
3. Image updates propagate within one TTL window -- at expiry, the tag flows through unmutated and kubelet pulls the latest.
4. Non-whitelisted images are completely unaffected -- the mutating webhook is a no-op for them.
5. If the mutating webhook fails, pods are still admitted normally via the validating webhook with a fresh cosign verify.

---

## Constraints

- Whitelist entries must be exact fully-qualified image names without tags -- no wildcards, no regexes.
- Minimum TTL is 60 seconds (enforced by Pydantic `ge=60` on `DigestPinEntry.ttl`).
- Mutating webhook timeout is 5 seconds -- mutation is a pure in-memory cache lookup, no network calls.
- `failurePolicy: Ignore` on the mutating webhook; `failurePolicy: Fail` on the validating webhook. The validating webhook is the security gate, the mutating webhook is an optimization.
- No containerd socket access, no systemd hardening changes, no `PrivateUsers` modifications.

---

## Output Format

1. `sek8s/clients/__init__.py` + `sek8s/clients/cosign.py` -- CosignClient moved from `sek8s/cosign/`, `verify()` returns `(bool, Optional[str])` with digest from cosign JSON output
2. `sek8s/config.py` -- `DigestPinEntry` model, `digest_pin_whitelist` field, `get_pin_ttl()` method on `CosignConfig`
3. `sek8s/image_utils.py` -- `strip_tag()` helper
4. `sek8s/validators/cosign.py` -- `_TagVerification` dataclass, `_tag_cache` dict, `get_pinned_digest()` method, tag cache population in `_verify_image_signature` for whitelisted images only
5. `sek8s/services/admission_models.py` -- `AdmissionStatus`, `AdmissionResponseBody`, `AdmissionReviewResponse` Pydantic models
6. `sek8s/services/admission_controller.py` -- `build_image_pin_patches()` on `AdmissionController`, real `handle_mutate()` on `AdmissionWebhookServer` with JSON Patch generation, typed response models throughout
7. `ansible/k3s/roles/admission-controller/templates/mutation-webhook.yaml.j2` -- `MutatingWebhookConfiguration` manifest
8. `ansible/k3s/roles/admission-controller/templates/cosign-registries.json.j2` -- `pin_digest_whitelist` section
9. `ansible/k3s/roles/admission-controller/tasks/configure-k3s-webhook.yml` -- deploy mutating webhook alongside validating webhook
10. `tests/unit/test_mutating_webhook.py` -- unit tests for digest extraction, whitelist lookup, tag cache, JSON Patch generation, end-to-end flow

---

## Failure Conditions

- Mutating webhook pins a digest for an image not in the whitelist.
- Cosign verification is skipped or short-circuited for any image.
- Tag-only image admitted during TTL window pulls a different image than what cosign verified (TOCTOU).
- Non-whitelisted images experience any behavioral change from the mutating webhook.
- Mutating webhook failure causes pod admission to be blocked (must be `failurePolicy: Ignore`).
- Wildcard or regex patterns accepted in the whitelist configuration.
- Image updates never propagate because the cache never expires.

---

## Rollout Notes

- Add entries to `pin_digest_whitelist` in the Ansible-templated `cosign-registries.json` for images that should be optimized. Start with `docker.io/parachutes/failed-chute-cleanup` (TTL 3600s).
- Deploy the new `MutatingWebhookConfiguration` manifest via the existing Ansible admission controller role. The mutating webhook is registered alongside the existing validating webhook.
- No changes needed to existing `cosign-registries.json` `registries` entries -- the whitelist is a separate, additive section.
- Feature is opt-in: an empty `pin_digest_whitelist` (the default) means the mutating webhook is a no-op for all images. Existing deployments are unaffected until whitelist entries are added.
- Monitor Docker Hub API call reduction via the debug logging and periodic summary (`/debug/summary` endpoint, `/var/log/admission-docker-hub-report.log`).

