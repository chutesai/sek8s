"""Tests for the attestation preflight (chutes_cvm.guest.preflight)."""

import hashlib
import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from chutes_cvm.guest import preflight
from chutes_cvm.guest.preflight import PreflightError, run_preflight, submit_profile

SAMPLE_PROFILE = json.dumps(
    {
        "hostname": "h",
        "timestamp": "t",
        "launch_determinism": {"qemu_version": "10.2.1", "cpu_args": "host"},
        "gpu": {"pci_device_ids": ["2335"], "count": 8},
        "cpu": {"total": 192, "sockets": 2, "cpu_vendor": "GenuineIntel"},
        "memory": {"total_gb": 2015},
        "numa": {"node_count": 2},
    }
)


def test_override_qemu_replaces_version():
    out = preflight._override_qemu(SAMPLE_PROFILE, "9.9.9")
    assert json.loads(out)["launch_determinism"]["qemu_version"] == "9.9.9"


def test_override_qemu_requires_block():
    with pytest.raises(PreflightError, match="launch_determinism"):
        preflight._override_qemu(json.dumps({"gpu": {}}), "9.9.9")


def test_load_creds_missing(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("miner: {}\n")
    with pytest.raises(PreflightError, match="ss58 / miner.seed"):
        preflight._load_miner_creds(str(cfg))


def test_load_creds_ok(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("miner:\n  ss58: 5ABC\n  seed: '0xdead'\n")
    assert preflight._load_miner_creds(str(cfg)) == ("5ABC", "0xdead")


def test_sign_message_format_and_headers():
    kp = MagicMock()
    kp.ss58_address = "5HOTKEY"
    kp.sign.return_value = b"\x01\x02\x03"
    with patch("chutes_cvm.guest.preflight.Keypair") as KP:
        KP.create_from_seed.return_value = kp
        hotkey, sig = preflight._sign("0xseed", b"body", "1700000000")
    assert hotkey == "5HOTKEY"
    assert sig == "010203"
    signed = kp.sign.call_args.args[0]
    assert signed == f"5HOTKEY:1700000000:{hashlib.sha256(b'body').hexdigest()}"


def _creds(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("miner:\n  ss58: 5HOTKEY\n  seed: '0xseed'\n")
    return str(cfg)


def test_run_preflight_flow_hits_preflight_endpoint(tmp_path):
    with patch(
        "chutes_cvm.guest.preflight._discover_profile_json", return_value=SAMPLE_PROFILE
    ), patch(
        "chutes_cvm.guest.preflight._sign", return_value=("5HOTKEY", "abcd")
    ), patch(
        "chutes_cvm.guest.preflight._post",
        return_value={"launchable": True, "fingerprint": "fp", "detail": "ok"},
    ) as post:
        resp = run_preflight(
            config_path=_creds(tmp_path), scripts_dir="/x", version="1.4.0", rc=False
        )
    assert resp["launchable"] is True
    # _post(path, api_base, hotkey, nonce, signature, body)
    path = post.call_args.args[0]
    assert path.startswith("/servers/tdx/preflight?")
    assert "version=1.4.0" in path and "rc=false" in path
    assert b"2335" in post.call_args.args[5]


def test_run_preflight_encodes_rc_true(tmp_path):
    with patch(
        "chutes_cvm.guest.preflight._discover_profile_json", return_value=SAMPLE_PROFILE
    ), patch(
        "chutes_cvm.guest.preflight._sign", return_value=("5HOTKEY", "abcd")
    ), patch(
        "chutes_cvm.guest.preflight._post", return_value={"launchable": False}
    ) as post:
        run_preflight(
            config_path=_creds(tmp_path), scripts_dir="/x", version="2.0.0", rc=True
        )
    assert "rc=true" in post.call_args.args[0]


def test_run_preflight_target_qemu_override(tmp_path):
    with patch(
        "chutes_cvm.guest.preflight._discover_profile_json", return_value=SAMPLE_PROFILE
    ), patch(
        "chutes_cvm.guest.preflight._sign", return_value=("5HOTKEY", "abcd")
    ), patch(
        "chutes_cvm.guest.preflight._post", return_value={"launchable": False}
    ) as post:
        run_preflight(
            config_path=_creds(tmp_path),
            scripts_dir="/x",
            version="1.4.0",
            rc=False,
            target_qemu="26.99",
        )
    body = json.loads(post.call_args.args[5].decode())
    assert body["launch_determinism"]["qemu_version"] == "26.99"


def test_submit_profile_hits_host_profiles_endpoint(tmp_path):
    with patch(
        "chutes_cvm.guest.preflight._discover_profile_json", return_value=SAMPLE_PROFILE
    ), patch(
        "chutes_cvm.guest.preflight._sign", return_value=("5HOTKEY", "abcd")
    ), patch(
        "chutes_cvm.guest.preflight._post",
        return_value={"status": "pending", "fingerprint": "fp", "stored": True},
    ) as post:
        resp = submit_profile(config_path=_creds(tmp_path), scripts_dir="/x")
    assert resp["stored"] is True
    assert post.call_args.args[0] == "/servers/tdx/host_profiles"


def test_post_http_error_surfaces_detail():
    err = urllib.error.HTTPError(
        "u",
        403,
        "Forbidden",
        {},
        io.BytesIO(json.dumps({"detail": "blacklisted"}).encode()),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(PreflightError, match="403.*blacklisted"):
            preflight._post(
                "/servers/tdx/preflight", "https://api", "hk", "n", "sig", b"{}"
            )


def test_post_unreachable_fails_closed_message():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with pytest.raises(PreflightError, match="unreachable"):
            preflight._post(
                "/servers/tdx/preflight", "https://api", "hk", "n", "sig", b"{}"
            )
