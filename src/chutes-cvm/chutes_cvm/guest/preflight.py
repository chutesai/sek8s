"""Attestation preflight — ask the control plane whether this host class can launch.

The miner never computes or matches a topology fingerprint: it captures its raw
platform metadata (``discover-profile.sh``), signs it with the miner hotkey, and POSTs
it to ``api.chutes.ai``. The API computes the fingerprint and answers with a status:

    accepted  — a published measurement covers this host class; it can launch
    pending   — submitted, awaiting measurement generation
    unknown   — neither (only via dry_run; a real submission is parked -> pending)

This replaces the old in-repo ``known_topologies`` match. The API owns the fingerprint
and the accept decision; if that key ever changes it changes there, not here.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from substrateinterface import Keypair, KeypairType

DEFAULT_API_BASE = "https://api.chutes.ai"

# Status -> exit code. accepted launches (0); pending/unknown are "not yet" (2); a
# transport/auth failure fails CLOSED (1) — the boot's LUKS key release needs the API
# anyway, so refusing to launch loses nothing.
_STATUS_EXIT = {"accepted": 0, "pending": 2, "unknown": 2}
FAIL_CLOSED = 1


class PreflightError(Exception):
    """Any failure that prevents getting a status (bad config, transport, API error)."""


def _load_miner_creds(config_path: str) -> "tuple[str, str]":
    """(ss58, seed) from the launch config.yaml's ``miner`` block."""
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
    except OSError as exc:
        raise PreflightError(f"cannot read config {config_path}: {exc}") from exc
    miner = cfg.get("miner") or {}
    ss58 = str(miner.get("ss58") or "").strip()
    seed = str(miner.get("seed") or "").strip()
    if not ss58 or not seed:
        raise PreflightError(f"{config_path} is missing miner.ss58 / miner.seed")
    return ss58, seed


def _discover_profile_json(scripts_dir: str) -> str:
    """Run ``discover-profile.sh --json-only`` and return the profile JSON text.

    The script writes a JSON file and prints its path (last stdout line); we read it,
    then delete it — the profile is transient, only the POST needs it.
    """
    script = Path(scripts_dir) / "discover-profile.sh"
    if not script.exists():
        raise PreflightError(f"discover-profile.sh not found at {script}")
    proc = subprocess.run(
        ["bash", str(script), "--json-only"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise PreflightError(
            f"discover-profile.sh failed: {proc.stderr.strip() or 'no output'}"
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise PreflightError("discover-profile.sh produced no JSON file path")
    path = Path(lines[-1].strip())
    try:
        data = path.read_text()
    except OSError as exc:
        raise PreflightError(
            f"cannot read discover-profile output {path}: {exc}"
        ) from exc
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    return data


def _override_qemu(profile_json: str, qemu_version: str) -> str:
    """Return the profile with launch_determinism.qemu_version replaced.

    Used by the pre-upgrade check (--target-os): the fingerprint depends on the QEMU the
    guest ACPI is generated with, so to ask "will my topology attest under the QEMU the
    upgrade brings?" we swap in the target QEMU before the API fingerprints it.
    """
    try:
        doc = json.loads(profile_json)
    except json.JSONDecodeError as exc:
        raise PreflightError(
            f"discover-profile output is not valid JSON: {exc}"
        ) from exc
    ld = doc.get("launch_determinism")
    if not isinstance(ld, dict):
        raise PreflightError(
            "discover-profile output has no launch_determinism block to override"
        )
    ld["qemu_version"] = qemu_version
    # Compact separators keep the signed body small; key order is irrelevant to the API.
    return json.dumps(doc, separators=(",", ":"))


def _sign(seed: str, body: bytes, nonce: str) -> "tuple[str, str]":
    """Sign ``{ss58}:{nonce}:{sha256(body)}`` with the miner hotkey (sr25519).

    Returns (ss58, signature_hex). The ss58 is derived from the seed (so it always matches
    the signature); the API verifies the signature against the hotkey header and confirms
    the hotkey is registered + un-blacklisted.
    """
    try:
        kp = Keypair.create_from_seed(seed, crypto_type=KeypairType.SR25519)
    except Exception as exc:
        raise PreflightError(f"invalid miner seed: {exc}") from exc
    body_hash = hashlib.sha256(body).hexdigest()
    signature = kp.sign(f"{kp.ss58_address}:{nonce}:{body_hash}")
    return kp.ss58_address, signature.hex()


def _post(
    api_base: str, hotkey: str, nonce: str, signature: str, body: bytes, dry_run: bool
) -> dict:
    """POST the signed profile; return the parsed {fingerprint, status, stored, detail}."""
    url = f"{api_base.rstrip('/')}/servers/tdx/host_profiles"
    if dry_run:
        url += "?dry_run=true"
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
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = f"HTTP {exc.code}"
        try:
            err = json.loads(exc.read().decode())
            detail = (
                err.get("detail") or err.get("message") or err.get("error") or detail
            )
        except Exception:
            pass
        raise PreflightError(f"API rejected the submission ({exc.code}): {detail}")
    except urllib.error.URLError as exc:
        raise PreflightError(f"API unreachable at {api_base}: {exc.reason}")
    except (ValueError, json.JSONDecodeError) as exc:
        raise PreflightError(f"API returned an unparseable response: {exc}")


def run_preflight(
    config_path: str,
    scripts_dir: str,
    api_base: str = DEFAULT_API_BASE,
    dry_run: bool = False,
    target_qemu: "str | None" = None,
) -> dict:
    """Discover -> sign -> POST -> status. Returns the API response dict; raises
    PreflightError on any failure to reach a status (caller fails closed)."""
    ss58, seed = _load_miner_creds(config_path)
    profile_json = _discover_profile_json(scripts_dir)
    if target_qemu:
        profile_json = _override_qemu(profile_json, target_qemu)
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
    return _post(api_base, hotkey, nonce, signature, body, dry_run)


def status_exit_code(status: "str | None") -> int:
    """READY(0) for accepted; WARNING(2) for pending/unknown/other."""
    return _STATUS_EXIT.get(status or "", 2)
