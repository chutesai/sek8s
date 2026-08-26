"""Tests for the `chutes-cvm host <verb>` dispatcher (chutes_cvm.host.cli).

verify / submit-profile route to the shared gate flow (chutes_cvm.guest.verify.verify_host,
with submit False/True); tune / restore call the tuning helpers; setup forwards to host.setup.
"""

from unittest.mock import patch

from chutes_cvm.host import cli as hostcli


def test_verify_runs_gate_without_submit():
    with patch("chutes_cvm.guest.verify.verify_host", return_value=0) as vh:
        assert hostcli.main(["verify", "--target-os", "26.04"]) == 0
    assert vh.call_args.kwargs["submit"] is False
    assert vh.call_args.kwargs["target_os"] == "26.04"


def test_submit_profile_sets_submit_true():
    with patch("chutes_cvm.guest.verify.verify_host", return_value=2) as vh:
        assert hostcli.main(["submit-profile"]) == 2
    assert vh.call_args.kwargs["submit"] is True
    assert vh.call_args.kwargs["target_os"] is None


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
