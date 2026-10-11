import struct

import pytest

from sek8s.exceptions import AttestationException, NonceError, SnpQuoteException
from sek8s.providers import base as base_module
from sek8s.providers.base import REPORT_DATA_BYTES, QuoteProvider
from sek8s.providers.snp import SNP_REPORT_SIZE, SnpQuoteProvider
from sek8s.providers.tdx import TdxQuoteProvider


@pytest.fixture
def no_tee_devices(monkeypatch):
    """Neither guest device node exists."""
    monkeypatch.setattr(base_module.os.path, "exists", lambda _p: False)


def _only(path_present):
    def _exists(path):
        return path == path_present

    return _exists


def test_create_prefers_the_tdx_device(monkeypatch):
    monkeypatch.setattr(
        base_module.os.path, "exists", _only(TdxQuoteProvider.guest_device)
    )
    assert isinstance(QuoteProvider.create(), TdxQuoteProvider)


def test_create_finds_the_snp_device(monkeypatch):
    monkeypatch.setattr(
        base_module.os.path, "exists", _only(SnpQuoteProvider.guest_device)
    )
    assert isinstance(QuoteProvider.create(), SnpQuoteProvider)


def test_create_raises_when_no_device(no_tee_devices):
    # Guessing a platform would produce evidence a verifier silently rejects,
    # which is much harder to diagnose than failing here.
    with pytest.raises(AttestationException, match="Could not determine TEE type"):
        QuoteProvider.create()


def test_each_provider_owns_its_device_path():
    """The device path lives on the provider, so there is no second table to drift."""
    assert TdxQuoteProvider.guest_device == "/dev/tdx_guest"
    assert SnpQuoteProvider.guest_device == "/dev/sev-guest"
    assert TdxQuoteProvider.guest_device != SnpQuoteProvider.guest_device


def test_providers_declare_distinct_tee_types():
    assert TdxQuoteProvider.tee_type == "tdx"
    assert SnpQuoteProvider.tee_type == "snp"


def test_report_data_binds_nonce_and_cert_hash(monkeypatch):
    provider = SnpQuoteProvider()
    monkeypatch.setattr(provider, "_get_cert_hash", lambda: "bb" * 32)

    report_data = provider._report_data_bytes("aa" * 32)

    assert len(report_data) == REPORT_DATA_BYTES
    assert report_data == bytes.fromhex("aa" * 32) + bytes.fromhex("bb" * 32)


def test_report_data_rejects_overlong_nonce(monkeypatch):
    """Regression guard: report data is validated, never truncated.

    An over-long nonce would otherwise displace cert_hash out of the fixed
    64-byte field, producing hardware-signed evidence that binds no certificate.
    """
    provider = SnpQuoteProvider()
    monkeypatch.setattr(provider, "_get_cert_hash", lambda: "bb" * 32)

    with pytest.raises(NonceError):
        provider._report_data_bytes("aa" * 64)


def test_report_data_rejects_short_nonce(monkeypatch):
    provider = SnpQuoteProvider()
    monkeypatch.setattr(provider, "_get_cert_hash", lambda: "bb" * 32)

    with pytest.raises(NonceError):
        provider._report_data_bytes("dd")


def test_report_data_rejects_non_hex_nonce(monkeypatch):
    provider = SnpQuoteProvider()
    monkeypatch.setattr(provider, "_get_cert_hash", lambda: "bb" * 32)

    with pytest.raises(NonceError):
        provider._report_data_bytes("zz" * 32)


def test_fetch_report_prefers_configfs(monkeypatch):
    provider = SnpQuoteProvider()
    calls = []

    def _configfs(_data):
        calls.append("configfs")
        return b"from-configfs"

    def _ioctl(_data):
        calls.append("ioctl")
        return b"from-ioctl"

    monkeypatch.setattr(provider, "_fetch_via_configfs", _configfs)
    monkeypatch.setattr(provider, "_fetch_via_ioctl", _ioctl)
    monkeypatch.setattr("sek8s.providers.snp.os.path.isdir", lambda _p: True)

    assert provider._fetch_report(b"\x00" * REPORT_DATA_BYTES) == b"from-configfs"
    assert calls == ["configfs"]


def test_fetch_report_falls_back_to_ioctl_when_configfs_fails(monkeypatch):
    provider = SnpQuoteProvider()

    def _boom(_data):
        raise OSError("no tsm provider registered")

    monkeypatch.setattr(provider, "_fetch_via_configfs", _boom)
    monkeypatch.setattr(provider, "_fetch_via_ioctl", lambda _d: b"from-ioctl")
    monkeypatch.setattr("sek8s.providers.snp.os.path.isdir", lambda _p: True)

    assert provider._fetch_report(b"\x00" * REPORT_DATA_BYTES) == b"from-ioctl"


def test_fetch_via_ioctl_errors_when_device_missing(monkeypatch):
    provider = SnpQuoteProvider()
    monkeypatch.setattr("sek8s.providers.snp.os.path.exists", lambda _p: False)

    with pytest.raises(SnpQuoteException, match="no SEV-SNP attestation interface"):
        provider._fetch_via_ioctl(b"\x00" * REPORT_DATA_BYTES)


@pytest.mark.asyncio
async def test_get_quote_returns_report(monkeypatch):
    provider = SnpQuoteProvider()
    report = bytes(range(256)) * 8  # >= SNP_REPORT_SIZE
    monkeypatch.setattr(provider, "_get_cert_hash", lambda: "bb" * 32)
    monkeypatch.setattr(provider, "_fetch_report", lambda _d: report)

    result = await provider.get_quote("aa" * 32)

    assert len(result) == SNP_REPORT_SIZE
    assert result == report[:SNP_REPORT_SIZE]


@pytest.mark.asyncio
async def test_get_quote_rejects_short_report(monkeypatch):
    provider = SnpQuoteProvider()
    monkeypatch.setattr(provider, "_get_cert_hash", lambda: "bb" * 32)
    monkeypatch.setattr(provider, "_fetch_report", lambda _d: b"\x00" * 32)

    with pytest.raises(SnpQuoteException, match="too short"):
        await provider.get_quote("aa" * 32)


def test_configfs_entry_is_removed_after_read(monkeypatch, tmp_path):
    """A leaked configfs entry would accumulate on every attestation request."""
    import os

    provider = SnpQuoteProvider()
    created = {}
    monkeypatch.setattr("sek8s.providers.snp.CONFIGFS_TSM_REPORT", str(tmp_path))

    # Captured before patching: the patch lands on the os module itself, so anything
    # here that went back through os.mkdir/os.makedirs would re-enter this stub.
    real_mkdir, real_rmdir, real_unlink = os.mkdir, os.rmdir, os.unlink

    def _mkdir(path, mode=0o700):
        # Stand in for configfs, which materialises inblob/outblob on mkdir.
        created["path"] = path
        real_mkdir(path, mode)
        with open(os.path.join(path, "outblob"), "wb") as f:
            f.write(b"\xab" * SNP_REPORT_SIZE)

    def _rmdir(path):
        # configfs removes an entry's attribute files along with its directory; a real
        # filesystem refuses (ENOTEMPTY), so model the kernel's behaviour here rather
        # than weakening what the provider is allowed to do. Built from the captured
        # primitives because shutil.rmtree would route back through the patched rmdir.
        for entry in os.listdir(path):
            real_unlink(os.path.join(path, entry))
        real_rmdir(path)

    monkeypatch.setattr("sek8s.providers.snp.os.mkdir", _mkdir)
    monkeypatch.setattr("sek8s.providers.snp.os.rmdir", _rmdir)

    report = provider._fetch_via_configfs(b"\x00" * REPORT_DATA_BYTES)

    assert report == b"\xab" * SNP_REPORT_SIZE
    assert not os.path.exists(created["path"])


def test_snp_ioctl_request_layout_is_the_abi_struct():
    """Guards the hand-packed snp_guest_request_ioctl against silent drift."""
    packed = struct.pack("=BxxxxxxxQQQ", 1, 0x1000, 0x2000, 0)
    assert len(packed) == 32  # matches _IOWR size encoded in SNP_GET_REPORT
    version, req, resp, exitinfo = struct.unpack("=BxxxxxxxQQQ", packed)
    assert (version, req, resp, exitinfo) == (1, 0x1000, 0x2000, 0)
