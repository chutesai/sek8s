"""Tests for chutes.guest.__main__ (run-td launcher)."""

from unittest.mock import MagicMock, patch

import chutes.guest.__main__ as guest_main
from chutes.guest.qemu import QemuCommand

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


@patch("chutes.guest.__main__.direct_boot_artifacts", return_value=("/k", "/i", "root=UUID=x ro"))
@patch("chutes.guest.__main__.verify_host_qemu_supported")
@patch("chutes.guest.__main__.subprocess.run")
@patch("chutes.guest.__main__.setup_passthrough")
@patch("chutes.guest.__main__.add_vsock")
@patch("chutes.guest.__main__.add_volumes")
@patch("chutes.guest.__main__.build_network")
@patch("chutes.guest.__main__.build_base_cmd", return_value=_FAKE_CMD)
def test_launch_vm_returns_qemu_nonzero(
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
