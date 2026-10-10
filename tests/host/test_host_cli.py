"""Tests for the `chutes-cvm host <verb>` dispatcher (chutes_cvm.host.cli).

verify runs the read-only gate flow (chutes_cvm.guest.verify.verify_host); submit-profile
registers the hardware profile directly (chutes_cvm.guest.chutes_api.submit_profile), independent
of the guest image; tune / restore call the tuning helpers; setup forwards to host.setup;
reset-gpus / vfio-wedged are host-hardware ops (GPUs, PCI subsystem).
"""

from unittest.mock import patch

import pytest
import topology_fixtures as known
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.host import cli as hostcli

_HOST = HostProfile.from_dict(known.rtx_numa_doc())


@pytest.fixture(autouse=True)
def _this_host():
    """submit-profile reads the host once; the reading is a property of the test box."""
    with patch(
        "chutes_cvm.guest.host_profile.HostProfile.from_host", return_value=_HOST
    ) as read:
        yield read


def test_verify_runs_gate_without_submit():
    with patch("chutes_cvm.guest.verify.verify_host", return_value=0) as vh:
        assert hostcli.main(["verify", "--target-os", "26.04"]) == 0
    assert vh.call_args.kwargs["submit"] is False
    assert vh.call_args.kwargs["target_os"] == "26.04"


def test_submit_profile_registers_directly_without_image_gate():
    # submit-profile posts the hardware profile directly — no verify_host / image readiness gate,
    # so a fresh host with no image downloaded can still register.
    with patch("chutes_cvm.guest.detection.verify_host_qemu_supported"), patch(
        "chutes_cvm.guest.chutes_api.submit_profile",
        return_value={"stored": True, "fingerprint": "fp123"},
    ) as sp, patch("chutes_cvm.guest.verify.verify_host") as vh:
        assert hostcli.main(["submit-profile"]) == 0
    assert sp.call_args.kwargs["host_profile"] is _HOST
    vh.assert_not_called()  # the image/readiness gate is bypassed for registration


def test_submit_profile_fails_when_the_host_cannot_be_read(_this_host, capsys):
    _this_host.side_effect = RuntimeError("lspci missing")
    with patch("chutes_cvm.guest.detection.verify_host_qemu_supported"), patch(
        "chutes_cvm.guest.chutes_api.submit_profile"
    ) as sp:
        assert hostcli.main(["submit-profile"]) == 1
    sp.assert_not_called()
    assert "cannot read this host: lspci missing" in capsys.readouterr().out


def test_submit_profile_forwards_target_os():
    """The pre-upgrade registration: --target-os must reach submit_profile, which rewrites the
    profile's OS/QEMU/-cpu args to the target release rather than the live host's."""
    with patch(
        "chutes_cvm.guest.chutes_api.submit_profile",
        return_value={"stored": True, "fingerprint": "fp123"},
    ) as sp:
        assert hostcli.main(["submit-profile", "--target-os", "26.04"]) == 0
    assert sp.call_args.kwargs["target_os"] == "26.04"


def test_submit_profile_rejects_unsupported_target_os():
    with patch("chutes_cvm.guest.chutes_api.submit_profile") as sp:
        assert hostcli.main(["submit-profile", "--target-os", "99.99"]) == 1
    sp.assert_not_called()


def test_submit_profile_without_target_os_uses_the_live_host():
    with patch("chutes_cvm.guest.detection.verify_host_qemu_supported"), patch(
        "chutes_cvm.guest.chutes_api.submit_profile",
        return_value={"stored": True, "fingerprint": "fp123"},
    ) as sp:
        assert hostcli.main(["submit-profile"]) == 0
    assert sp.call_args.kwargs["target_os"] is None


def test_submit_profile_blocks_an_unsupported_live_os(capsys):
    """Registering the live host registers its OS+QEMU, so an unbaselined release must not
    reach the API — it would burn a generation slot on a class that can never attest."""
    with patch(
        "chutes_cvm.guest.detection.verify_host_qemu_supported",
        side_effect=ValueError("Host OS release '25.10' is not supported"),
    ), patch("chutes_cvm.guest.chutes_api.submit_profile") as sp:
        assert hostcli.main(["submit-profile"]) == 1
    sp.assert_not_called()
    assert "--target-os" in capsys.readouterr().out  # points at the pre-upgrade path


def test_submit_profile_with_target_os_skips_the_live_qemu_gate():
    """--target-os is exactly the case where the live QEMU is wrong and about to be replaced."""
    with patch(
        "chutes_cvm.guest.detection.verify_host_qemu_supported",
        side_effect=ValueError("wrong qemu"),
    ), patch(
        "chutes_cvm.guest.chutes_api.submit_profile",
        return_value={"stored": True, "fingerprint": "fp123"},
    ) as sp:
        assert hostcli.main(["submit-profile", "--target-os", "26.04"]) == 0
    assert sp.call_args.kwargs["target_os"] == "26.04"


def test_tune_dispatches():
    with patch("chutes_cvm.host.tune.apply_tuning") as ap:
        assert hostcli.main(["tune"]) == 0
    ap.assert_called_once()


def test_restore_dispatches():
    with patch("chutes_cvm.host.tune.restore_tuning") as rt:
        assert hostcli.main(["restore"]) == 0
    rt.assert_called_once()


def test_setup_forwards_to_setup_main():
    with patch("chutes_cvm.host.setup.main", return_value=0) as sm:
        assert hostcli.main(["setup", "--noninteractive"]) == 0
    assert sm.call_args.args[0] == ["--noninteractive"]


@pytest.mark.parametrize(
    "device_id, sbr_args",
    [
        ("2331", ["--reset-with-sbr", "--reset-after-cc-mode-switch"]),  # H100 PCIe
        ("2bb5", ["--reset-with-sbr", "--reset-after-cc-mode-switch"]),  # RTX PRO 6000
        ("2335", ["--reset-with-sbr", "--reset-after-ppcie-mode-switch"]),  # H200
    ],
)
def test_reset_gpus_runs_the_reset_the_profile_chooses(device_id, sbr_args):
    """The CC-vs-PPCIe choice is the profile's, the same one a launch makes; the script only
    runs it. It used to keep its own device list, which sent any card missing from it to PPCIe.
    """
    with patch(
        "chutes_cvm.guest.detection.detect_gpu_device_ids", return_value={device_id}
    ), patch("chutes_cvm.host.cli._run_script", return_value=0) as run:
        assert hostcli.main(["reset-gpus"]) == 0
    assert run.call_args.args == ("devices/reset-gpus.sh", sbr_args)


@pytest.mark.parametrize("ids", [set(), {"2331", "2335"}, {"dead"}])
def test_reset_gpus_refuses_a_host_it_cannot_profile(ids, capsys):
    with patch(
        "chutes_cvm.guest.detection.detect_gpu_device_ids", return_value=ids
    ), patch("chutes_cvm.host.cli._run_script") as run:
        assert hostcli.main(["reset-gpus"]) == 1
    run.assert_not_called()
    assert "reset-gpus" in capsys.readouterr().err


def test_vfio_wedged_maps_predicate_to_exit_code():
    with patch("chutes_cvm.vfio.pci_operations_wedged", return_value=True):
        assert hostcli.main(["vfio-wedged"]) == 0
    with patch("chutes_cvm.vfio.pci_operations_wedged", return_value=False):
        assert hostcli.main(["vfio-wedged"]) == 1


@pytest.mark.parametrize(
    "vendor, platform", [("GenuineIntel", "tdx"), ("AuthenticAMD", "snp")]
)
def test_platform_prints_what_the_cpu_runs(vendor, platform, capsys):
    with patch("chutes_cvm.guest.detection.detect_cpu_vendor", return_value=vendor):
        assert hostcli.main(["platform"]) == 0
    assert capsys.readouterr().out.strip() == platform


def test_platform_check_fails_with_the_platforms_remedy(capsys):
    """Ansible's post-reboot gate: the same check a launch makes, and the per-vendor hint."""
    with patch(
        "chutes_cvm.guest.detection.detect_cpu_vendor", return_value="AuthenticAMD"
    ), patch("chutes_cvm.guest.tee._module_param_enabled", return_value=False):
        assert hostcli.main(["platform", "--check"]) == 1
    err = capsys.readouterr().err
    assert "SEV-SNP" in err and "SMEE" in err


def test_platform_check_passes_when_enabled(capsys):
    with patch(
        "chutes_cvm.guest.detection.detect_cpu_vendor", return_value="AuthenticAMD"
    ), patch("chutes_cvm.guest.tee._module_param_enabled", return_value=True):
        assert hostcli.main(["platform", "--check"]) == 0
    assert capsys.readouterr().out.strip() == "snp"


def test_platform_refuses_an_unknown_cpu(capsys):
    with patch("chutes_cvm.guest.detection.detect_cpu_vendor", return_value=""):
        assert hostcli.main(["platform"]) == 1


def _blocker(detail="reclaim running; ETA ~102 min"):
    from chutes_cvm.guest.vm import Blocker

    return Blocker(
        name="td-reclaim",
        summary="reclaiming",
        detail=detail,
        clears_itself=True,
        overridable=False,
    )


def test_devices_free_exits_zero_when_nothing_holds_them(capsys):
    with patch("chutes_cvm.guest.vm.device_blockers", return_value=[]):
        assert hostcli.main(["devices-free"]) == 0
    assert "Nothing holds" in capsys.readouterr().out


def test_devices_free_exits_nonzero_and_prints_every_reason(capsys):
    with patch(
        "chutes_cvm.guest.vm.device_blockers",
        return_value=[_blocker("reason one"), _blocker("reason two")],
    ):
        assert hostcli.main(["devices-free"]) == 1
    err = capsys.readouterr().err
    assert "reason one" in err and "reason two" in err


def test_devices_free_uses_the_conventional_exit_sense():
    """vfio-wedged exits 0 when it finds a problem; devices-free exits 0 when it finds none.
    Getting these the same way round would make every `if` around them read backwards.
    """
    with patch("chutes_cvm.guest.vm.device_blockers", return_value=[_blocker()]), patch(
        "chutes_cvm.vfio.pci_operations_wedged", return_value=True
    ):
        assert hostcli.main(["devices-free"]) == 1  # blocked -> nonzero
        assert hostcli.main(["vfio-wedged"]) == 0  # wedged  -> zero


def test_bundled_scripts_do_not_reimplement_qemu_detection():
    """Four copies of "is a chutes-td QEMU running?" had drifted apart, every one matching
    /proc/<pid>/cmdline and skipping zombies -- so every one reported the host idle while a
    QEMU that had powered its guest off still held all twelve devices. reset-gpus.sh acting on
    that answer SBR-reset GPUs underneath a live process. The answer now has one owner
    (guest.vm.device_blockers, via `host devices-free`), so a bundled script must not grow its
    own again."""
    from chutes_cvm.paths import SCRIPTS_DIR

    offenders = []
    for script in SCRIPTS_DIR.rglob("*.sh"):
        text = script.read_text()
        if "chutes-td" in text and "pgrep" in text:
            offenders.append(script.relative_to(SCRIPTS_DIR))
    assert not offenders, (
        f"{offenders} match chutes-td with pgrep; call `chutes-cvm host devices-free` instead "
        "(a zombie's cmdline is empty, so pgrep cannot see the state that matters)"
    )
