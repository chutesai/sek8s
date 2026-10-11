"""Tests for graceful VM shutdown (chutes_cvm.guest.shutdown).

Signs a purpose-based request with the miner hotkey from config and POSTs it to the guest
system-manager shutdown endpoint. urllib is mocked; the sr25519 signing is real (the throwaway
fixture hotkey).
"""

import io
import urllib.error
from unittest.mock import patch

import hotkey_fixtures as hk
import pytest
import yaml
from chutes_cvm.guest.shutdown import ShutdownError, graceful_shutdown
from substrateinterface import Keypair


def _write_cfg(tmp_path, *, miner=None, vm_ip="192.168.100.2") -> str:
    data = {"network": {"vm_ip": vm_ip}}
    data["miner"] = {"ss58": hk.SS58, "seed": hk.SEED} if miner is None else miner
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data))
    return str(p)


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b""


@pytest.mark.parametrize(
    "miner",
    [
        {"ss58": hk.SS58, "seed": hk.SEED},
        {"ss58": hk.SS58, "private_key": hk.PRIVATE_KEY},
    ],
    ids=["seed", "private-key"],
)
def test_graceful_posts_signed_request(tmp_path, miner):
    captured = {}

    def _urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp()

    with patch(
        "chutes_cvm.guest.shutdown.urllib.request.urlopen", side_effect=_urlopen
    ):
        ip = graceful_shutdown(_write_cfg(tmp_path, miner=miner, vm_ip="10.0.0.9"))

    assert ip == "10.0.0.9"
    assert captured["url"] == "http://10.0.0.9:8080/status/system/shutdown"
    assert captured["method"] == "POST"
    h = captured["headers"]
    assert h["x-chutes-hotkey"] == hk.SS58
    message = f"{hk.SS58}:{h['x-chutes-nonce']}:status"
    assert Keypair(ss58_address=hk.SS58).verify(
        message, bytes.fromhex(h["x-chutes-signature"])
    )


def test_graceful_without_a_key_raises(tmp_path):
    with pytest.raises(ShutdownError, match="no miner.private_key or miner.seed"):
        graceful_shutdown(_write_cfg(tmp_path, miner={"ss58": hk.SS58}))


def test_graceful_with_a_key_for_another_hotkey_raises(tmp_path):
    other = Keypair.create_from_seed("0x" + "11" * 32).ss58_address
    with pytest.raises(ShutdownError, match="not miner.ss58"):
        graceful_shutdown(
            _write_cfg(tmp_path, miner={"ss58": other, "private_key": hk.PRIVATE_KEY})
        )


def test_graceful_http_error_raises(tmp_path):
    def _urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"go away")
        )

    with patch(
        "chutes_cvm.guest.shutdown.urllib.request.urlopen", side_effect=_urlopen
    ):
        with pytest.raises(ShutdownError, match="401"):
            graceful_shutdown(_write_cfg(tmp_path))


def test_graceful_unreachable_raises(tmp_path):
    def _urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    with patch(
        "chutes_cvm.guest.shutdown.urllib.request.urlopen", side_effect=_urlopen
    ):
        with pytest.raises(ShutdownError, match="could not reach"):
            graceful_shutdown(_write_cfg(tmp_path))
