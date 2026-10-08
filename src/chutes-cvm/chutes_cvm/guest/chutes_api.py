"""Signed requests to the Chutes API about this host's class.

The miner never computes or matches a topology fingerprint: it signs its ``HostProfile`` with the
miner hotkey and POSTs it to ``api.chutes.ai``, which owns the fingerprint and every verdict. The
caller reads the host and passes the profile in, so every request is about the reading the caller
acts on. Two of the host-profile operations live here:

    host_class_status()         — POST /servers/{tdx,snp}/host_profiles/status: is this topology
                                  known, and which published images cover it? Version-free, so it
                                  answers on a host that has downloaded nothing yet. The profile's
                                  platform picks the route -> ``HostClass.fetch``
    submit_profile()            — POST /servers/tdx/host_profiles: register an unmeasured class so
                                  Chutes generates its measurements

(GET /servers/tdx/host_profiles is the generator's/third-party listing — not here.)

`host verify` and `guest launch` ask the same question: the class's measured images. Verify asks
before any image is downloaded; launch then looks up the ``(version, rc)`` it holds
(``HostClass.measured_image``), and an SEV-SNP launch takes that image's ACPI hash from the answer.
The API owns the fingerprint; if that key ever changes it changes there, not here.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request

import yaml
from chutes_cvm.guest.detection import SUPPORTED_QEMU_BY_OS
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.paths import DEFAULT_API_BASE
from substrateinterface import Keypair, KeypairType

# A transport/auth failure (no verdict) fails CLOSED — the boot's LUKS key release needs the API
# anyway, so refusing to launch when we cannot confirm loses nothing.
FAIL_CLOSED = 1


class ChutesApiError(Exception):
    """A request to the Chutes API produced no usable answer: bad config, transport, an API
    error, or a response missing what the caller needs."""


def _load_miner_creds(config_path: str) -> "tuple[str, str]":
    """(ss58, seed) from the launch config.yaml's ``miner`` block."""
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
    except OSError as exc:
        raise ChutesApiError(f"cannot read config {config_path}: {exc}") from exc
    miner = cfg.get("miner") or {}
    ss58 = str(miner.get("ss58") or "").strip()
    seed = str(miner.get("seed") or "").strip()
    if not ss58 or not seed:
        raise ChutesApiError(f"{config_path} is missing miner.ss58 / miner.seed")
    return ss58, seed


def _apply_target_os(profile_json: str, target_os: str) -> str:
    """Return the profile rewritten as if this host were already on ``target_os``.

    Used by the pre-upgrade path (--target-os): the OS release is what picks the QEMU that
    generates the guest ACPI measured into RTMR0, so every OS-derived field has to move
    together. Rewriting the release while leaving the live host's QEMU behind would register
    the class against a (release, QEMU) pair that does not exist — e.g. a 25.10 host asking
    about 26.04 would submit "26.04 + QEMU 10.1.0", which 26.04 never ships.
    """
    qemu_version = SUPPORTED_QEMU_BY_OS.get(target_os)
    if qemu_version is None:
        raise ChutesApiError(
            f"target OS {target_os!r} is not supported {sorted(SUPPORTED_QEMU_BY_OS)}; "
            f"supported releases ship a QEMU whose RTMR0 is baselined."
        )
    try:
        doc = json.loads(profile_json)
    except json.JSONDecodeError as exc:
        raise ChutesApiError(
            f"discover-profile output is not valid JSON: {exc}"
        ) from exc
    qemu = doc.get("qemu")
    if not isinstance(qemu, dict):
        raise ChutesApiError("host profile has no qemu block to override")
    qemu["qemu_version"] = qemu_version
    # Compact separators keep the signed body small; key order is irrelevant to the API.
    return json.dumps(doc, separators=(",", ":"), sort_keys=True)


def _sign(seed: str, body: bytes, nonce: str) -> "tuple[str, str]":
    """Sign ``{ss58}:{nonce}:{sha256(body)}`` with the miner hotkey (sr25519).

    Returns (ss58, signature_hex). The ss58 is derived from the seed (so it always matches
    the signature); the API verifies the signature against the hotkey header and confirms
    the hotkey is registered + un-blacklisted.
    """
    try:
        kp = Keypair.create_from_seed(seed, crypto_type=KeypairType.SR25519)
    except Exception as exc:
        raise ChutesApiError(f"invalid miner seed: {exc}") from exc
    body_hash = hashlib.sha256(body).hexdigest()
    signature = kp.sign(f"{kp.ss58_address}:{nonce}:{body_hash}")
    return kp.ss58_address, signature.hex()


def _post(
    path: str, api_base: str, hotkey: str, nonce: str, signature: str, body: bytes
) -> dict:
    """POST the signed profile body to ``path`` on the API; return the parsed JSON dict."""
    url = f"{api_base.rstrip('/')}{path}"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "chutes-cvm-preflight/1.0",
            "X-Chutes-Hotkey": hotkey,
            "X-Chutes-Nonce": nonce,
            "X-Chutes-Signature": signature,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = f"HTTP {exc.code}"
        try:
            err = json.loads(exc.read().decode())
            detail = (
                err.get("detail") or err.get("message") or err.get("error") or detail
            )
        except Exception:  # nosec B110
            pass
        raise ChutesApiError(f"API rejected the request ({exc.code}): {detail}")
    except urllib.error.URLError as exc:
        raise ChutesApiError(f"API unreachable at {api_base}: {exc.reason}")
    except (ValueError, json.JSONDecodeError) as exc:
        raise ChutesApiError(f"API returned an unparseable response: {exc}")


def _signed_profile(
    config_path: str,
    target_os: "str | None",
    host_profile: "HostProfile",
) -> "tuple[str, str, str, bytes]":
    """Discover this host's profile and sign it with the miner hotkey.

    Returns (hotkey, nonce, signature, body) for a POST. ``target_os`` rewrites the profile's
    OS-derived fields (release, QEMU, -cpu args) first, for the pre-upgrade check. Shared by the
    preflight check and the submit path.

    ``host_profile`` is required, not defaulted: every caller is asking about ONE host and has
    already read it, so a default would only be a second reading that could disagree with the
    one being signed. A launch passes the profile it will boot, which is what makes the verdict
    a verdict about the shape that actually launches.
    """
    ss58, seed = _load_miner_creds(config_path)
    profile_json = host_profile.to_api_json()
    if target_os:
        profile_json = _apply_target_os(profile_json, target_os)
    body = profile_json.encode()
    nonce = str(int(time.time()))
    hotkey, signature = _sign(seed, body, nonce)
    if ss58 and hotkey != ss58:
        # Non-fatal: the seed is authoritative for the signature, but a mismatch means the
        # configured ss58 is wrong — surface it so the operator can fix the config.
        print(
            f"  warning: config miner.ss58 ({ss58}) does not match the seed's hotkey ({hotkey}); "
            "signing with the seed's hotkey."
        )
    return hotkey, nonce, signature, body


def host_class_status(
    config_path: str,
    host_profile: "HostProfile",
    api_base: str = DEFAULT_API_BASE,
    target_os: "str | None" = None,
) -> dict:
    """Sign ``host_profile`` -> POST its platform's /servers/{tdx,snp}/host_profiles/status -> the
    raw host class.

    ``HostClass.fetch`` is the caller; it parses the answer into that platform's typed measured
    images.

    The version-free question behind `host verify`: is this topology known, and which published
    images cover it? Deliberately takes no version — a host is verified before it has downloaded
    any image, so nothing here may depend on what is on disk.

    Returns {fingerprint, status, measurements: [{version, rc, ...}, ...], detail}. Raises
    ChutesApiError on any failure to reach a verdict.
    """
    hotkey, nonce, signature, body = _signed_profile(
        config_path, target_os, host_profile
    )
    path = f"/servers/{host_profile.tee_provider.name}/host_profiles/status"
    return _post(path, api_base, hotkey, nonce, signature, body)


def submit_profile(
    config_path: str,
    host_profile: "HostProfile",
    api_base: str = DEFAULT_API_BASE,
    target_os: "str | None" = None,
) -> dict:
    """Sign ``host_profile`` -> POST /servers/tdx/host_profiles -> register.

    Stores this host class so Chutes generates its measurements. Returns
    {fingerprint, status, stored, detail}; raises ChutesApiError on failure. Run when the preflight
    reports the class is not yet launchable. ``target_os`` registers the class the host will BE
    after an OS upgrade (target release + the QEMU it ships), not the one it is on now.
    """
    hotkey, nonce, signature, body = _signed_profile(
        config_path, target_os, host_profile
    )
    return _post("/servers/tdx/host_profiles", api_base, hotkey, nonce, signature, body)
