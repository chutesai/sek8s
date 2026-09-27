"""Tests for the `chutes-cvm host <verb>` dispatcher (chutes_cvm.host.cli).

verify runs the read-only gate flow (chutes_cvm.guest.verify.verify_host); submit-profile
registers the hardware profile directly (chutes_cvm.guest.preflight.submit_profile), independent
of the guest image; tune / restore call the tuning helpers; setup forwards to host.setup;
reset-gpus / vfio-wedged are host-hardware ops (GPUs, PCI subsystem).
"""

from unittest.mock import patch

import pytest
from chutes_cvm.host import cli as hostcli


def test_verify_runs_gate_without_submit():
    with patch("chutes_cvm.guest.verify.verify_host", return_value=0) as vh:
        assert hostcli.main(["verify", "--target-os", "26.04"]) == 0
    assert vh.call_args.kwargs["submit"] is False
    assert vh.call_args.kwargs["target_os"] == "26.04"


def test_submit_profile_registers_directly_without_image_gate():
    # submit-profile posts the hardware profile directly — no verify_host / image readiness gate,
    # so a fresh host with no image downloaded can still register.
    with patch("chutes_cvm.guest.detection.verify_host_qemu_supported"), patch(
        "chutes_cvm.guest.preflight.submit_profile",
        return_value={"stored": True, "fingerprint": "fp123"},
    ) as sp, patch("chutes_cvm.guest.verify.verify_host") as vh:
        assert hostcli.main(["submit-profile"]) == 0
    assert sp.called
    vh.assert_not_called()  # the image/readiness gate is bypassed for registration


def test_submit_profile_forwards_target_os():
    """The pre-upgrade registration: --target-os must reach submit_profile, which rewrites the
    profile's OS/QEMU/-cpu args to the target release rather than the live host's."""
    with patch(
        "chutes_cvm.guest.preflight.submit_profile",
        return_value={"stored": True, "fingerprint": "fp123"},
    ) as sp:
        assert hostcli.main(["submit-profile", "--target-os", "26.04"]) == 0
    assert sp.call_args.kwargs["target_os"] == "26.04"


def test_submit_profile_rejects_unsupported_target_os():
    with patch("chutes_cvm.guest.preflight.submit_profile") as sp:
        assert hostcli.main(["submit-profile", "--target-os", "99.99"]) == 1
    sp.assert_not_called()


def test_submit_profile_without_target_os_uses_the_live_host():
    with patch("chutes_cvm.guest.detection.verify_host_qemu_supported"), patch(
        "chutes_cvm.guest.preflight.submit_profile",
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
    ), patch("chutes_cvm.guest.preflight.submit_profile") as sp:
        assert hostcli.main(["submit-profile"]) == 1
    sp.assert_not_called()
    assert "--target-os" in capsys.readouterr().out  # points at the pre-upgrade path


def test_submit_profile_with_target_os_skips_the_live_qemu_gate():
    """--target-os is exactly the case where the live QEMU is wrong and about to be replaced."""
    with patch(
        "chutes_cvm.guest.detection.verify_host_qemu_supported",
        side_effect=ValueError("wrong qemu"),
    ), patch(
        "chutes_cvm.guest.preflight.submit_profile",
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
