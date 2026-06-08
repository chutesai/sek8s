"""Unit tests for QEMU NUMA topology helpers."""

from unittest.mock import patch

import pytest

from chutes.guest.qemu import (
    PcieRootPinning,
    _append_numa_memory,
    _parse_mem_mib,
    add_volumes,
    build_base_cmd,
    use_numa_topology,
)


@pytest.mark.parametrize(
    ("mem", "expected_mib"),
    [
        ("1536G", 1536 * 1024),
        ("512M", 512),
    ],
)
def test_parse_mem_mib(mem, expected_mib):
    assert _parse_mem_mib(mem) == expected_mib


def test_parse_mem_mib_rejects_invalid():
    with pytest.raises(ValueError, match="Invalid memory size"):
        _parse_mem_mib("1.5G")


def test_use_numa_topology_requires_two_host_nodes():
    with patch("chutes.guest.qemu.host_numa_nodes", return_value=[0, 1]):
        assert use_numa_topology(True) is True
        assert use_numa_topology(False) is False


def test_use_numa_topology_falls_back_for_non_dual_node():
    with patch("chutes.guest.qemu.host_numa_nodes", return_value=[0]):
        assert use_numa_topology(True) is False


def test_build_base_cmd_numa_adds_per_node_backends(tmp_path):
    img = tmp_path / "disk.qcow2"
    img.write_bytes(b"")
    with patch("chutes.guest.qemu.host_numa_nodes", return_value=[0, 1]):
        cmd = build_base_cmd(
            mem="1024G",
            smp_topology="188,sockets=2,cores=94,threads=1",
            process_name="chutes-td",
            cpu_args="host,-avx10",
            firmware="/tmp/TDVF.fd",
            img_path=str(img),
            foreground=True,
            pidfile="/tmp/pid",
            logfile="/tmp/log",
            enable_numa_topology=True,
        )
    flat = " ".join(cmd)
    assert "memory-backend-ram,id=mem-node0" in flat
    assert "memory-backend-ram,id=mem-node1" in flat
    assert "host-nodes=0,policy=bind" in flat
    assert "host-nodes=1,policy=bind" in flat
    assert "-numa node,nodeid=0,memdev=mem-node0" in flat
    assert "-numa dist,src=0,dst=1,val=21" in flat
    assert "memory-backend=mem0" not in flat


def test_append_numa_memory_splits_remainder_on_last_node():
    cmd: list[str] = []
    _append_numa_memory(cmd, mem_mib=1537, host_nodes=[0, 1])
    assert "size=768M" in " ".join(cmd)
    assert "size=769M" in " ".join(cmd)


def test_config_volume_uses_explicit_virtio_blk_not_legacy_if_virtio(tmp_path):
    config = tmp_path / "config.qcow2"
    config.write_bytes(b"")
    pinning = PcieRootPinning(True)
    cmd: list[str] = []
    add_volumes(cmd, config_volume=str(config), cache_volume=None, storage_volume=None, pci_pinning=pinning)
    flat = " ".join(cmd)
    assert "if=virtio" not in flat
    assert "virtio-config" in flat
    assert "virtio-blk-pci,drive=virtio-config,bus=pcie.0" in flat


def test_pcie_root_pinning_assigns_unique_slots():
    pinning = PcieRootPinning(True)
    assert pinning.device_suffix() == ",bus=pcie.0,addr=0x2"
    assert pinning.device_suffix() == ",bus=pcie.0,addr=0x3"
