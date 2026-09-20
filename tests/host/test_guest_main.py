"""Tests for chutes_cvm.guest.__main__ (the low-level QEMU boot primitive driven by
`chutes-cvm guest launch`; not a CLI command)."""

from unittest.mock import MagicMock, patch

import chutes_cvm.guest.__main__ as guest_main
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
@patch("chutes_cvm.guest.__main__.HostProfile.from_host")
def test_launch_vm_returns_qemu_nonzero(
    mock_from_host,
    _mock_base,
    _mock_net,
    _mock_vol,
    _mock_vsock,
    _mock_pt,
    mock_run,
    _mock_qemu_check,
    _mock_stage,
):
    from argparse import Namespace

    import topology_fixtures as known
    from chutes_cvm.guest.host_profile import HostProfile

    mock_from_host.return_value = HostProfile(known.rtx_numa_doc())
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
    assert guest_main.launch_vm(args) == 1


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
@patch("chutes_cvm.guest.__main__.HostProfile.from_host")
def test_launch_takes_cpu_args_from_the_host_profile(
    mock_from_host,
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
    mock_from_host.return_value = HostProfile(doc)
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
        )
    )

    passed = mock_base.call_args.kwargs["cpu_args"]
    assert passed == "host,-avx10,-tsx"
    assert passed != GUEST_CPU_ARGS


def test_discover_profile_reports_the_launch_cpu_args():
    """discover-profile.sh re-spells the -cpu args in bash. They feed the host profile the
    control plane fingerprints, so a drift from what the launcher actually passes would
    baseline a class against CPUID leaves no VM ever boots with."""
    script = (SCRIPTS_DIR / "discover-profile.sh").read_text()
    assert f'CPU_ARGS="{GUEST_CPU_ARGS}"' in script
