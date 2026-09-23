"""Tests for chutes_cvm.guest.__main__ (the low-level QEMU boot primitive driven by
`chutes-cvm guest launch`; not a CLI command)."""

from unittest.mock import MagicMock, patch

import chutes_cvm.guest.__main__ as guest_main
import chutes_cvm.guest.tee as tee_module
import pytest
from chutes_cvm.guest.detection import GUEST_CPU_ARGS
from chutes_cvm.guest.qemu import QemuCommand
from chutes_cvm.paths import SCRIPTS_DIR

_FAKE_CMD = QemuCommand(
    mem="1G",
    smp_topology="1",
    cpu_args="host",
    machine="q35",
    firmware="/x",
    process_name="t",
    foreground=True,
    logfile="/l",
    pidfile="/p",
)


# The provider comes from the profile the caller hands in (an Intel doc -> TDX), so only the
# CAPABILITY check is stubbed: it reads this machine's kvm module parameters, which are a
# property of the box running the tests, not of the code under test.
@patch("chutes_cvm.guest.tee.TeeProvider.verify_environment")
@patch(
    "chutes_cvm.guest.__main__.direct_boot_artifacts",
    return_value=("/k", "/i", "root=UUID=x ro"),
)
@patch("chutes_cvm.guest.__main__.verify_host_qemu_supported")
@patch("chutes_cvm.guest.__main__.proc.run")
@patch("chutes_cvm.guest.__main__.setup_passthrough")
@patch("chutes_cvm.guest.__main__.add_vsock")
@patch("chutes_cvm.guest.__main__.add_volumes")
@patch("chutes_cvm.guest.__main__.build_network")
@patch("chutes_cvm.guest.__main__.build_base_cmd", return_value=_FAKE_CMD)
def test_launch_vm_returns_qemu_nonzero(
    _mock_base,
    _mock_net,
    _mock_vol,
    _mock_vsock,
    _mock_pt,
    mock_run,
    _mock_qemu_check,
    _mock_stage,
    _mock_tee,
):
    from argparse import Namespace

    import topology_fixtures as known
    from chutes_cvm.guest.host_profile import HostProfile

    host = HostProfile(known.rtx_numa_doc())
    mock_run.return_value = MagicMock(returncode=1)
    args = Namespace(
        image="/tmp/fake.img",
        pass_gpus=False,
        foreground=True,
        config_volume=None,
        cache_volume=None,
        storage_volume=None,
        ssh_port=10022,
        network_type="user",
        net_iface=None,
        net_queues=4,
    )
    assert guest_main.launch_vm(args, host) == 1


@patch(
    "chutes_cvm.guest.__main__.direct_boot_artifacts",
    return_value=("/k", "/i", "root=UUID=x ro"),
)
@patch("chutes_cvm.guest.__main__.verify_host_qemu_supported")
@patch("chutes_cvm.guest.__main__.proc.run")
@patch("chutes_cvm.guest.__main__.setup_passthrough")
@patch("chutes_cvm.guest.__main__.add_vsock")
@patch("chutes_cvm.guest.__main__.add_volumes")
@patch("chutes_cvm.guest.__main__.build_network")
@patch("chutes_cvm.guest.__main__.build_base_cmd", return_value=_FAKE_CMD)
# As above: the profile decides the platform; this only stubs the host capability probe.
@patch("chutes_cvm.guest.tee.TeeProvider.verify_environment")
def test_launch_takes_cpu_args_from_the_host_profile(
    _mock_tee2,
    mock_base,
    _mock_net,
    _mock_vol,
    _mock_vsock,
    _mock_pt,
    mock_run,
    _mock_qemu_check,
    _mock_stage,
    monkeypatch,
):
    """A launch must pass the -cpu the profile resolves, as generation does.

    Both go through `HostProfile.cpu_args`. The launcher used the bare GUEST_CPU_ARGS constant,
    which agreed only because the table holds a single entry mapping to that same constant -- so
    the day a second QEMU version maps to different args, a host on it
    would boot with one -cpu having been measured with another, and fail attestation with nothing
    in the command to show why.
    """
    from argparse import Namespace

    import topology_fixtures as known
    from chutes_cvm.guest.host_profile import HostProfile

    monkeypatch.setitem(HostProfile.CPU_ARGS_BY_QEMU, "11.0.0", "host,-avx10,-tsx")
    doc = known.rtx_numa_doc()
    doc["qemu"]["qemu_version"] = "11.0.0"
    host = HostProfile(doc)
    mock_run.return_value = MagicMock(returncode=0)

    guest_main.launch_vm(
        Namespace(
            image="/tmp/fake.img",
            pass_gpus=True,
            foreground=True,
            config_volume=None,
            cache_volume=None,
            storage_volume=None,
            ssh_port=10022,
            network_type="user",
            net_iface=None,
            net_queues=4,
            ssh=False,
            clean=False,
        ),
        host,
    )

    # The launcher hands over the profile itself rather than a copy of its -cpu, so the
    # assertion is that the profile reaching the builder resolves the right args. That is
    # now structural -- build_base_cmd reads them off the profile -- but the launcher
    # could still pass the wrong profile, which is what this catches.
    passed_profile = mock_base.call_args.args[0]
    assert passed_profile.cpu_args == "host,-avx10,-tsx"
    assert passed_profile.cpu_args != GUEST_CPU_ARGS
    assert mock_base.call_args.kwargs.get("cpu_args") is None


def test_discover_profile_reports_the_launch_cpu_args():
    """discover-profile.sh re-spells the -cpu args in bash. They feed the host profile the
    control plane fingerprints, so a drift from what the launcher actually passes would
    baseline a class against CPUID leaves no VM ever boots with."""
    script = (SCRIPTS_DIR / "discover-profile.sh").read_text()
    assert f'CPU_ARGS="{GUEST_CPU_ARGS}"' in script


@patch("chutes_cvm.guest.__main__.verify_host_qemu_supported")
@patch(
    "chutes_cvm.guest.tee._module_param_enabled",
    side_effect=lambda path: path == tee_module.KVM_INTEL_TDX,
)
def test_launch_refuses_when_the_host_contradicts_the_profile(
    _mock_param, _mock_qemu_check
):
    """An AMD profile on a machine reporting TDX means the profile was captured
    elsewhere, or SEV-SNP is off in BIOS. Either way the guest would be measured
    against a platform it is not booting on, so refuse before any VM work.

    The real ``verify_environment`` runs here -- only the kvm parameter read is stubbed,
    since that is a property of the box running the tests, not of the code under test.
    """
    from argparse import Namespace

    import topology_fixtures as known
    from chutes_cvm.guest.host_profile import HostProfile

    doc = known.rtx_numa_doc()
    doc["cpu"]["vendor"] = "AuthenticAMD"
    host = HostProfile(doc)

    with pytest.raises(RuntimeError, match="but the machine reports"):
        guest_main.launch_vm(
            Namespace(
                image="/tmp/fake.img",
                pass_gpus=False,
                foreground=True,
                config_volume=None,
                cache_volume=None,
                storage_volume=None,
                ssh_port=10022,
                network_type="user",
                net_iface=None,
                net_queues=4,
            ),
            host,
        )
