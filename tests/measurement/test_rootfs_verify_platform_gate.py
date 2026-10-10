"""The post-mount ``rootfs-verify`` gates on the TEE exactly as initramfs ``rootfs-measure`` does.

TDX guest: verify against hardware RTMR3. SEV-SNP guest: no RTMR3, so verify the live root
against the list the initramfs checked against the canonical manifest and recorded in /run.
Neither device: not a confidential VM, fail closed.
"""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "ansible/guest/roles/rootfs-measure/files/rootfs-verify"
# In the guest the helper is this module, copied to /usr/local/lib/sek8s/rtmr3.py.
_HELPER_DIR = _REPO / "src/chutes-cvm/chutes_cvm/measurement"


class _Device:
    def __init__(self, present):
        self.present = present

    def is_char_device(self):
        return self.present

    def __str__(self):
        return "/dev/fake"


@pytest.fixture
def verify(monkeypatch):
    """Load the script as a module, with the guest's helper import satisfied.

    Bytecode is off before loading: the loader would otherwise write a .pyc into the role's
    files/ directory, which the image build copies from.
    """
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(sys, "path", [str(_HELPER_DIR), *sys.path])
    loader = importlib.machinery.SourceFileLoader("rootfs_verify", str(_SCRIPT))
    spec = importlib.util.spec_from_loader("rootfs_verify", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    fatals = []
    monkeypatch.setattr(module, "fatal", fatals.append)
    module.fatals = fatals
    return module


def _platform(monkeypatch, module, *, tdx, sev):
    monkeypatch.setattr(module, "TDX_GUEST_DEVICE", _Device(tdx))
    monkeypatch.setattr(module, "SEV_GUEST_DEVICE", _Device(sev))


def test_tdx_guest_is_verified_against_hardware(verify, monkeypatch):
    _platform(monkeypatch, verify, tdx=True, sev=False)
    assert verify.platform() == "tdx"
    assert verify.fatals == []


def test_snp_guest_takes_the_snp_path(verify, monkeypatch):
    _platform(monkeypatch, verify, tdx=False, sev=True)
    assert verify.platform() == "snp"
    assert verify.fatals == []


def test_no_tee_device_fails_closed(verify, monkeypatch):
    _platform(monkeypatch, verify, tdx=False, sev=False)
    assert verify.platform() is None
    assert len(verify.fatals) == 1
    assert "not running in a TDX guest" in verify.fatals[0]


def test_snp_main_never_reads_hardware_rtmr3(verify, monkeypatch):
    """k3s Requires= this unit, so the SNP path must exit cleanly once verified, and must not
    reach the TDX quote path that powered SNP guests off."""
    _platform(monkeypatch, verify, tdx=False, sev=True)
    checked = []
    monkeypatch.setattr(verify, "verify_snp", lambda: checked.append(True))

    def _must_not_run():
        raise AssertionError("read hardware RTMR3 on a guest with none")

    monkeypatch.setattr(verify, "read_hardware_rtmr3", _must_not_run)
    with pytest.raises(SystemExit) as exc:
        verify.main()
    assert exc.value.code == 0
    assert checked == [True]


def test_tdx_main_verifies_against_hardware_and_not_the_snp_record(verify, monkeypatch):
    _platform(monkeypatch, verify, tdx=True, sev=False)
    checked = []
    monkeypatch.setattr(verify, "verify_tdx", lambda: checked.append("tdx"))
    monkeypatch.setattr(verify, "verify_snp", lambda: checked.append("snp"))
    with pytest.raises(SystemExit) as exc:
        verify.main()
    assert exc.value.code == 0
    assert checked == ["tdx"]
