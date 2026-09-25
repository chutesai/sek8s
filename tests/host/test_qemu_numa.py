"""Unit tests for QEMU NUMA topology helpers."""

import re

import pytest
import topology_fixtures as known
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.qemu import (
    DirectBoot,
    GuestNetwork,
    GuestVolumes,
    LaunchCommandBuilder,
    PassthroughSet,
    PcieRootPinning,
    ProcessBundle,
    QemuCommand,
    _parse_mem_mib,
)

# create() defaults none of the values that differ between a launch and a measurement, so the
# launch-shaped ones are named once here.
_LAUNCH = dict(
    boot=DirectBoot(
        kernel="/boot/vmlinuz", initrd="/boot/initrd.img", cmdline="root=UUID=x ro"
    ),
    net=GuestNetwork(network_type="user", ssh_port=10022),
    volumes=GuestVolumes(),
    process=ProcessBundle(name="chutes-td", foreground=True),
    passthrough=PassthroughSet(),
)


def _empty_cmd() -> QemuCommand:
    """A minimal QemuCommand for exercising a single builder in isolation."""
    return QemuCommand(
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


def test_launch_command_numa_adds_per_node_backends(tmp_path):
    img = tmp_path / "disk.qcow2"
    img.write_bytes(b"")
    cmd = QemuCommand.create(
        known.QemuProfileStub(
            mem="1024G",
            smp_topology="188,sockets=2,cores=94,threads=1",
            uses_guest_numa=True,
        ),
        firmware="/tmp/TDVF.fd",
        img_path=str(img),
        host_nodes=[0, 1],
        **_LAUNCH,
    )
    flat = " ".join(cmd.to_args())
    assert "memory-backend-ram,id=mem-node0" in flat
    assert "memory-backend-ram,id=mem-node1" in flat
    assert "host-nodes=0,policy=bind" in flat
    assert "host-nodes=1,policy=bind" in flat
    assert "-numa node,nodeid=0,memdev=mem-node0" in flat
    assert "-numa dist,src=0,dst=1,val=21" in flat
    assert "memory-backend=mem0" not in flat
    # prealloc must NOT be set under TDX: it pins a second full copy of guest
    # RAM (guest_memfd serves the real private pages), ~2x usage -> host OOM.
    assert "prealloc" not in flat


def test_launch_command_pins_smbios_identity(tmp_path):
    """SMBIOS type 1/2/3 identity is pinned so per-server motherboard
    differences don't shift RTMR0 within a profile. This builder is the single
    source of truth — the offline measurement path reads it too."""
    img = tmp_path / "disk.qcow2"
    img.write_bytes(b"")
    cmd = QemuCommand.create(
        known.QemuProfileStub(
            mem="512G",
            smp_topology="94,sockets=1,cores=94,threads=1",
            uses_guest_numa=False,
        ),
        firmware="/tmp/TDVF.fd",
        img_path=str(img),
        host_nodes=[],
        **_LAUNCH,
    )
    flat = " ".join(cmd.to_args())
    assert (
        "type=1,manufacturer=Chutes,product=TDX-VM,version=1.0,serial=0,"
        "uuid=00000000-0000-0000-0000-000000000000" in flat
    )
    assert "type=2,manufacturer=Chutes,product=TDX-VM,version=1.0,serial=0" in flat
    assert "type=3,manufacturer=Chutes,version=1.0,serial=0" in flat


def _builder(**stub):
    """A LaunchCommandBuilder over a stub profile, for exercising one traversal step."""
    stub.setdefault("mem", "8G")
    stub.setdefault("smp_topology", "4,sockets=1,cores=4,threads=1")
    stub.setdefault("uses_guest_numa", True)
    return LaunchCommandBuilder(known.QemuProfileStub(**stub))


def test_numa_memory_splits_remainder_on_last_node():
    cmd = _empty_cmd()
    _builder()._numa_memory(cmd, mem_mib=1537, host_nodes=[0, 1])
    assert "size=768M" in " ".join(cmd.objects)
    assert "size=769M" in " ".join(cmd.objects)


def test_direct_boot_emits_kernel_initrd_append_and_drops_bootindex(tmp_path):
    img = tmp_path / "disk.qcow2"
    img.write_bytes(b"")
    cmd = QemuCommand.create(
        known.QemuProfileStub(
            mem="512G",
            smp_topology="94,sockets=1,cores=94,threads=1",
            uses_guest_numa=False,
        ),
        firmware="/tmp/TDVF.fd",
        img_path=str(img),
        host_nodes=[],
        **{
            **_LAUNCH,
            "boot": DirectBoot(
                kernel="/boot/vmlinuz",
                initrd="/boot/initrd.img",
                cmdline="root=UUID=abc ro console=ttyS0",
            ),
        },
    )
    args = cmd.to_args()
    flat = " ".join(args)
    # Direct-boot args present, cmdline pinned verbatim.
    assert "-kernel" in args and "/boot/vmlinuz" in args
    assert "-initrd" in args and "/boot/initrd.img" in args
    assert args[args.index("-append") + 1] == "root=UUID=abc ro console=ttyS0"
    # qcow2 is still attached as the (LUKS) root disk, just not the boot device.
    assert "file=" + str(img) in flat
    assert "virtio-blk-pci,drive=virtio-disk0" in flat
    assert "bootindex" not in flat


def test_config_volume_uses_explicit_virtio_blk_not_legacy_if_virtio(tmp_path):
    config = tmp_path / "config.qcow2"
    config.write_bytes(b"")
    cmd = _empty_cmd()
    _builder()._volumes(cmd, GuestVolumes(config=str(config)), PcieRootPinning(True))
    flat = " ".join(cmd.drives + cmd.devices)
    assert "if=virtio" not in flat
    assert "virtio-config" in flat
    assert "virtio-blk-pci,drive=virtio-config,bus=pcie.0" in flat


def test_pcie_root_pinning_assigns_unique_slots():
    pinning = PcieRootPinning(True)
    assert pinning.device_suffix() == ",bus=pcie.0,addr=0x2"
    assert pinning.device_suffix() == ",bus=pcie.0,addr=0x3"


# ---------------------------------------------------------------------------
# Device presence vs backing — a device occupies a measured pcie.0 slot whether
# or not anything backs it, so offline generation needs one without the other.
# ---------------------------------------------------------------------------


def test_network_device_does_not_depend_on_having_a_host_interface():
    """The NIC is unconditional; only the netdev backing it follows the inputs. Without an
    interface the device is emitted alone -- what measurement generation needs, since the device
    takes a measured pcie.0 slot while a netdev is not a PCI device at all.

    No mode flag: same function, different inputs, deterministic output.
    """
    with_iface, without = _empty_cmd(), _empty_cmd()
    builder = _builder(uses_guest_numa=False)
    builder._network(
        with_iface,
        GuestNetwork(network_type="tap", net_iface="br0", ssh_port=22),
        PcieRootPinning(False),
    )
    builder._network(
        without,
        GuestNetwork(network_type="tap", net_iface=None, ssh_port=22),
        PcieRootPinning(False),
    )
    assert without.devices == with_iface.devices
    assert without.netdevs == [] and len(with_iface.netdevs) == 1


def test_measurement_fills_the_same_slots_a_launch_occupies():
    """Both commands come from one traversal, so the emulated devices land on identical pcie.0
    slots -- which is what the DSDT records and RTMR0 measures. What differs is only WHAT sits in
    each slot: a launch attaches real virtio devices, the dump a backing-free filler, because the
    dumper has no drives or netdevs to reference.

    Add or remove a volume and both move together: there is one call site, so a slot cannot go
    missing from one command and not the other.
    """

    def emulated(cmd):
        infra = ("pxb-pcie", "pcie-root-port", "vfio-pci", "pci-bar-stub")
        return [d for d in cmd.devices if not d.startswith(infra)]

    def slots(cmd):
        return [re.search(r"addr=(0x[0-9a-f]+)", d).group(1) for d in emulated(cmd)]

    host = HostProfile(known.rtx_numa_doc())
    launch = QemuCommand.create(
        host,
        firmware="/f",
        img_path="/root.qcow2",
        host_nodes=[0, 1],
        boot=DirectBoot(kernel="/k", initrd="/i", cmdline=""),
        net=GuestNetwork(network_type="tap", net_iface="br0", ssh_port=22),
        volumes=GuestVolumes(config="/c.qcow2", cache="/k.raw", storage="/s.raw"),
        process=ProcessBundle(name="chutes-td"),
        passthrough=PassthroughSet.from_profile(host),
    )
    generated = QemuCommand.for_measurement(host, firmware="/f")

    # Root disk, NIC, three volumes, vsock -- below the PXB bridges at 0x18+.
    assert slots(launch) == [f"0x{slot:x}" for slot in range(2, 8)]
    assert slots(generated) == slots(launch)

    assert all(d.startswith("virtio-rng-pci") for d in emulated(generated))
    assert not any(d.startswith("virtio-rng-pci") for d in emulated(launch))
