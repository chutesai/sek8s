"""image_config rewrites a host's own QEMU command into tdx-measure metadata.

These assert the structural rewrites (machine, memory, emulated-device fillers,
vfio->pci-bar-stub swap, serial) that make an offline dump reproduce a real
launch's measured ACPI. The byte-exact acceptance (== box-028) runs in the
tdx-measure container, not here.
"""

from dataclasses import replace

import pytest
import topology_fixtures as known
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.qemu import MeasurementCommandBuilder, QemuCommand
from chutes_cvm.measurement.image_config import ImageConfig

_FW = "/opt/ovmf/OVMF.fd"


def _md(doc, **kw):
    host = HostProfile(doc)
    cmd = QemuCommand.for_measurement(host, firmware=_FW)
    return ImageConfig(cmd, host, acpi_tables="/out/acpi.bin", **kw).to_dict()


def _rtx_numa():
    return _md(known.rtx_numa_doc())


def test_machine_is_rewritten_to_non_tdx():
    q = _rtx_numa()["boot_config"]["qemu"]
    assert q["machine"] == "q35,kernel_irqchip=split,smm=off,pic=off"
    assert not any("tdx-guest" in o for o in q["objects"])


def test_memory_backends_reserve_off_and_unbound():
    q = _rtx_numa()["boot_config"]["qemu"]
    backends = [o for o in q["objects"] if o.startswith("memory-backend-ram")]
    assert backends
    for o in backends:
        assert "reserve=off" in o  # maps multi-TB RAM on a small host
        assert "host-nodes=" not in o and "policy=bind" not in o


def test_emulated_slots_filled_and_boot_disk_dropped():
    q = _rtx_numa()["boot_config"]["qemu"]
    fillers = [d for d in q["devices"] if d.startswith("virtio-rng-pci")]
    slots = {d.split("addr=")[1] for d in fillers}
    assert slots == {f"0x{s:x}" for s in range(2, 8)}  # 0x2-0x7 populated
    assert not any("virtio-disk0" in d for d in q["devices"])  # boot disk gone


def test_vfio_swapped_for_pci_bar_stub_with_profile_bars():
    q = _rtx_numa()["boot_config"]["qemu"]
    assert not any(d.startswith("vfio-pci") for d in q["devices"])
    stubs = [d for d in q["devices"] if d.startswith("pci-bar-stub")]
    assert len(stubs) == 8  # one per GPU
    # each stub carries the RTX BAR layout and stays on its root port
    for i, stub in enumerate(
        sorted(stubs, key=lambda s: int(s.split("bus=rp")[1].split(",")[0])), 1
    ):
        assert f"bus=rp{i}," in stub
        assert "bars=0:64M:p64;2:128G:p64;4:32M:p64" in stub
        assert "vendor=0x10de" in stub


def test_serial_attached_for_com1():
    q = _rtx_numa()["boot_config"]["qemu"]
    assert q["serial"] == ["null"]


def test_smbios_can_be_dropped():
    with_it = _md(known.rtx_numa_doc(), with_smbios=True)
    without = _md(known.rtx_numa_doc(), with_smbios=False)
    assert with_it["boot_config"]["qemu"]["smbios"]
    assert without["boot_config"]["qemu"]["smbios"] == []


def test_flat_topology_generates():
    q = _md(known.rtx_flat_doc())["boot_config"]["qemu"]
    assert not any("pxb-pcie" in d for d in q["devices"])
    assert sum(d.startswith("pci-bar-stub") for d in q["devices"]) == 8


def test_boot_config_scalars():
    bc = _rtx_numa()["boot_config"]
    assert bc["cpus"] == 124
    assert bc["memory"] == "768G"
    assert bc["acpi_tables"] == "/out/acpi.bin"


def test_nvswitch_endpoint_modeled():
    # NVSwitch is a passthrough device too; its BARs shape the DSDT. H200 models it,
    # so each switch endpoint becomes a pci-bar-stub with the captured layout.
    doc = known.host_document(
        "H200",
        vcpus=124,
        gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1),
        nvswitch_nodes=(0, 1, 0, 1),
    )
    q = _md(doc)["boot_config"]["qemu"]
    nvsw = [
        d for d in q["devices"] if d.startswith("pci-bar-stub") and "bus=rp_nvsw" in d
    ]
    assert len(nvsw) == 4  # one per NVSwitch
    for stub in nvsw:
        assert "bars=0:32M:m64" in stub
        assert "device=0x22a3" in stub and "class=0x0680" in stub


def test_endpoint_without_captured_bars_raises():
    """The stub reproduces the guest's MMIO windows from the captured BARs, so without them the
    generated RTMR0 matches no real boot -- refuse rather than measure a wrong aperture.
    """
    host = HostProfile(
        known.host_document(
            "H200", vcpus=124, gpu_nodes=(0,) * 8, nvswitch_nodes=(0,) * 4
        )
    )
    bare = replace(host.gpus[0], bars=[])

    with pytest.raises(ValueError, match="no BARs captured"):
        MeasurementCommandBuilder(host).endpoint(bare, "rp_ib1")


def test_emulated_slot_fillers_keep_the_slots_the_command_assigned():
    """Fillers stand in for the launch's emulated devices at the *same* pcie.0 slots.

    Which slots those are is the builder's decision, not this adapter's: ``PcieRootPinning``
    states them on both paths -- 0x1 upward, or 0x2 upward under guest NUMA so they sit below the
    PXB bridges at 0x18+ -- and this only carries them across. Deciding it twice is what put every
    flat guest's DSDT device nodes one slot high, changing the ACPI digest and so RTMR0, which is
    why no flat class ever reproduced its real boot.

    Measured against live DSDTs from both paths on one host:
        NUMA  _ADR slots [2,3,4,5,6,7, 24,25, 31]
        FLAT  _ADR slots [1,2,3,4,5,6,  8, 9, 31]
    """
    numa = _md(known.rtx_numa_doc())["boot_config"]["qemu"]
    flat = _md(known.rtx_flat_doc())["boot_config"]["qemu"]

    def fillers(q):
        return [d for d in q["devices"] if d.startswith("virtio-rng-pci")]

    assert any("pxb-pcie" in d for d in numa["devices"])
    assert sorted(int(d.split("addr=")[1], 16) for d in fillers(numa)) == [
        2,
        3,
        4,
        5,
        6,
        7,
    ]

    assert not any("pxb-pcie" in d for d in flat["devices"])
    assert sorted(int(d.split("addr=")[1], 16) for d in fillers(flat)) == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]
