"""The post-mount ``rtmr3-verify`` gates on the TEE exactly as initramfs ``rtmr3-measure`` does.

TDX guest: verify. SEV-SNP guest: skip, since nothing was extended at init-bottom and there is
no runtime register to compare against. Neither device: not a confidential VM, fail closed.
Only the initramfs half had this gate, so an SNP guest reached the TDX quote path and a
production build would have powered itself off.
"""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "ansible/guest/roles/rtmr3-measure/files/rtmr3-verify"
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
    loader = importlib.machinery.SourceFileLoader("rtmr3_verify", str(_SCRIPT))
    spec = importlib.util.spec_from_loader("rtmr3_verify", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    fatals = []
    monkeypatch.setattr(module, "fatal", fatals.append)
    module.fatals = fatals
    return module


def _platform(monkeypatch, module, *, tdx, sev):
    monkeypatch.setattr(module, "TDX_GUEST_DEVICE", _Device(tdx))
    monkeypatch.setattr(module, "SEV_GUEST_DEVICE", _Device(sev))


def test_tdx_guest_is_verified(verify, monkeypatch):
    _platform(monkeypatch, verify, tdx=True, sev=False)
    assert verify.has_rtmr3() is True
    assert verify.fatals == []


def test_snp_guest_is_skipped_not_failed(verify, monkeypatch, capsys):
    _platform(monkeypatch, verify, tdx=False, sev=True)
    assert verify.has_rtmr3() is False
    assert verify.fatals == []
    assert "SEV-SNP has no runtime measurement register" in capsys.readouterr().out


def test_no_tee_device_fails_closed(verify, monkeypatch):
    _platform(monkeypatch, verify, tdx=False, sev=False)
    assert verify.has_rtmr3() is False
    assert len(verify.fatals) == 1
    assert "not running in a TDX guest" in verify.fatals[0]


def test_snp_main_exits_clean_before_measuring(verify, monkeypatch):
    """k3s Requires= this unit, so the skip must be a clean exit, and it must happen before
    the measure/quote path that powered the SNP guest off."""
    _platform(monkeypatch, verify, tdx=False, sev=True)

    def _must_not_run():
        raise AssertionError("measured on a guest with no RTMR3")

    monkeypatch.setattr(verify, "measure", _must_not_run)
    monkeypatch.setattr(verify, "read_hardware_rtmr3", _must_not_run)
    with pytest.raises(SystemExit) as exc:
        verify.main()
    assert exc.value.code == 0
