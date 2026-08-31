"""Tests for the `chutes-cvm host <verb>` dispatcher (chutes_cvm.host.cli).

verify runs the read-only gate flow (chutes_cvm.guest.verify.verify_host); submit-profile
registers the hardware profile directly (chutes_cvm.guest.preflight.submit_profile), independent
of the guest image; tune / restore call the tuning helpers; setup forwards to host.setup;
reset-gpus / vfio-wedged are host-hardware ops (GPUs, PCI subsystem).
"""

from unittest.mock import patch

from chutes_cvm.host import cli as hostcli


def test_verify_runs_gate_without_submit():
    with patch("chutes_cvm.guest.verify.verify_host", return_value=0) as vh:
        assert hostcli.main(["verify", "--target-os", "26.04"]) == 0
    assert vh.call_args.kwargs["submit"] is False
    assert vh.call_args.kwargs["target_os"] == "26.04"


def test_submit_profile_registers_directly_without_image_gate():
    # submit-profile posts the hardware profile directly — no verify_host / image readiness gate,
    # so a fresh host with no image downloaded can still register.
    with patch(
        "chutes_cvm.guest.preflight.submit_profile",
        return_value={"stored": True, "fingerprint": "fp123"},
    ) as sp, patch("chutes_cvm.guest.verify.verify_host") as vh:
        assert hostcli.main(["submit-profile"]) == 0
    assert sp.called
    vh.assert_not_called()  # the image/readiness gate is bypassed for registration


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


def test_reset_gpus_delegates_to_script():
    with patch("chutes_cvm.host.cli._run_script", return_value=0) as run:
        assert hostcli.main(["reset-gpus"]) == 0
    assert run.call_args.args[0] == "devices/reset-gpus.sh"


def test_vfio_wedged_maps_predicate_to_exit_code():
    with patch("chutes_cvm.vfio.pci_operations_wedged", return_value=True):
        assert hostcli.main(["vfio-wedged"]) == 0
    with patch("chutes_cvm.vfio.pci_operations_wedged", return_value=False):
        assert hostcli.main(["vfio-wedged"]) == 1
