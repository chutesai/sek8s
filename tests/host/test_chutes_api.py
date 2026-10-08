"""Tests for the attestation preflight (chutes_cvm.guest.chutes_api)."""

import hashlib
import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from chutes_cvm.guest import chutes_api
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.chutes_api import (
    ChutesApiError,
    host_class_status,
    submit_profile,
)


def _capture(**over):
    """A discover-profile capture: an 8-GPU H200 host on 25.10."""
    bars = [
        {"index": 0, "size_mb": 16, "kind": "p64"},
        {"index": 2, "size_mb": 262144, "kind": "p64"},
        {"index": 4, "size_mb": 32, "kind": "p64"},
    ]
    doc = {
        "hostname": "h",
        "timestamp": "t",
        "host": {"os_version_id": "25.10"},
        "qemu": {
            "qemu_version": "10.1.0",
            "qemu_version_full": "QEMU emulator version 10.1.0 (Debian 1:10.1.0+ds-1)",
        },
        "gpus": [
            {
                "bdf": f"0000:{0x19 + i:02x}:00.0",
                "vendor": "10de",
                "device_id": "2335",
                "pci_class": "0302",
                "numa_node": i // 4,
                "bars": list(bars),
            }
            for i in range(8)
        ],
        "nvswitches": [
            {
                "bdf": f"0000:{0x83 + i:02x}:00.0",
                "vendor": "10de",
                "device_id": "22a3",
                "pci_class": "0680",
                "numa_node": 1,
                "bars": [{"index": 0, "size_mb": 32, "kind": "m64"}],
            }
            for i in range(4)
        ],
        "ib_devices": [],
        "cpu": {
            "count": 128,
            "sockets": 2,
            "vendor": "GenuineIntel",
            "processor_id": "f2060c00fffba91f",
        },
        "memory": {"total_gb": 2015, "guest_gb": 1128},
        "numa": {"node_count": 2},
    }
    doc.update(over)
    return doc


# What submit-profile sends: the API subset, not the capture.
SAMPLE_PROFILE = HostProfile.from_dict(_capture()).to_api_json()


def test_apply_target_os_moves_the_qemu_the_release_ships():
    """The bug this guards: a 25.10 host asking about 26.04 must not register a class built with
    QEMU 10.1.0. The submitted profile carries the QEMU version rather than the release, so that
    version is what has to move."""
    out = json.loads(chutes_api._apply_target_os(SAMPLE_PROFILE, "26.04"))
    assert out["qemu"]["qemu_version"] == chutes_api.SUPPORTED_QEMU_BY_OS["26.04"]
    assert "host" not in out  # the release itself is not submitted


def test_apply_target_os_rejects_unsupported_release():
    with pytest.raises(ChutesApiError, match="not supported"):
        chutes_api._apply_target_os(SAMPLE_PROFILE, "99.99")


def test_apply_target_os_requires_block():
    with pytest.raises(ChutesApiError, match="qemu block"):
        chutes_api._apply_target_os(json.dumps({"gpu": {}}), "26.04")


def test_load_creds_missing(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("miner: {}\n")
    with pytest.raises(ChutesApiError, match="ss58 / miner.seed"):
        chutes_api._load_miner_creds(str(cfg))


def test_load_creds_ok(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("miner:\n  ss58: 5ABC\n  seed: '0xdead'\n")
    assert chutes_api._load_miner_creds(str(cfg)) == ("5ABC", "0xdead")


def test_sign_message_format_and_headers():
    kp = MagicMock()
    kp.ss58_address = "5HOTKEY"
    kp.sign.return_value = b"\x01\x02\x03"
    with patch("chutes_cvm.guest.chutes_api.Keypair") as KP:
        KP.create_from_seed.return_value = kp
        hotkey, sig = chutes_api._sign("0xseed", b"body", "1700000000")
    assert hotkey == "5HOTKEY"
    assert sig == "010203"
    signed = kp.sign.call_args.args[0]
    assert signed == f"5HOTKEY:1700000000:{hashlib.sha256(b'body').hexdigest()}"


def _creds(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("miner:\n  ss58: 5HOTKEY\n  seed: '0xseed'\n")
    return str(cfg)


def test_host_class_status_signs_the_given_profile_with_no_version(tmp_path):
    """Version-free: the class is asked about before any image is on disk."""
    host = HostProfile.from_dict(_capture())
    with patch(
        "chutes_cvm.guest.chutes_api._sign", return_value=("5HOTKEY", "abcd")
    ), patch(
        "chutes_cvm.guest.chutes_api._post", return_value={"status": "accepted"}
    ) as post:
        resp = host_class_status(
            config_path=_creds(tmp_path), host_profile=host
        )
    assert resp == {"status": "accepted"}
    # The API's route, spelled out: it comes from TeeProvider.name, so renaming a provider must
    # fail here rather than silently call a route the API does not serve.
    assert post.call_args.args[0] == "/servers/tdx/host_profiles/status"
    assert post.call_args.args[5] == host.to_api_json().encode()


def test_an_amd_host_asks_the_snp_route(tmp_path):
    """Each platform's classes are answered by its own route; the profile says which."""
    doc = _capture()
    doc["cpu"]["vendor"] = "AuthenticAMD"
    host = HostProfile.from_dict(doc)
    with patch(
        "chutes_cvm.guest.chutes_api._sign", return_value=("5HOTKEY", "abcd")
    ), patch(
        "chutes_cvm.guest.chutes_api._post", return_value={"status": "unknown"}
    ) as post:
        host_class_status(config_path=_creds(tmp_path), host_profile=host)
    # The API's route, spelled out (see the TDX case above).
    assert post.call_args.args[0] == "/servers/snp/host_profiles/status"


def test_host_class_status_target_os_override(tmp_path):
    host = HostProfile.from_dict(_capture())
    with patch(
        "chutes_cvm.guest.chutes_api._sign", return_value=("5HOTKEY", "abcd")
    ), patch(
        "chutes_cvm.guest.chutes_api._post", return_value={"status": "unknown"}
    ) as post:
        host_class_status(
            config_path=_creds(tmp_path), target_os="26.04", host_profile=host
        )
    body = json.loads(post.call_args.args[5].decode())
    assert body["qemu"]["qemu_version"] == "10.2.1"  # 26.04's QEMU, not the live 10.1.0
    assert "host" not in body


def test_submit_profile_hits_host_profiles_endpoint(tmp_path):
    # Like every request here, it signs the profile its caller read; it never reads the host.
    host = HostProfile.from_dict(_capture())
    with patch(
        "chutes_cvm.guest.chutes_api.HostProfile.from_host",
        side_effect=AssertionError("submit must not read the host itself"),
    ), patch(
        "chutes_cvm.guest.chutes_api._sign", return_value=("5HOTKEY", "abcd")
    ), patch(
        "chutes_cvm.guest.chutes_api._post",
        return_value={"status": "pending", "fingerprint": "fp", "stored": True},
    ) as post:
        resp = submit_profile(config_path=_creds(tmp_path), host_profile=host)
    assert resp["stored"] is True
    assert post.call_args.args[0] == "/servers/tdx/host_profiles"
    assert post.call_args.args[5] == host.to_api_json().encode()


def test_post_http_error_surfaces_detail():
    err = urllib.error.HTTPError(
        "u",
        403,
        "Forbidden",
        {},
        io.BytesIO(json.dumps({"detail": "blacklisted"}).encode()),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(ChutesApiError, match="403.*blacklisted"):
            chutes_api._post(
                "/servers/tdx/preflight", "https://api", "hk", "n", "sig", b"{}"
            )


def test_post_unreachable_fails_closed_message():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with pytest.raises(ChutesApiError, match="unreachable"):
            chutes_api._post(
                "/servers/tdx/preflight", "https://api", "hk", "n", "sig", b"{}"
            )
