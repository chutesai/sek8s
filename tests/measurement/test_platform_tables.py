"""platform_tables rewrites a measurement MachineSpec into tdx-measure metadata.

These assert the structural rewrites (machine, memory, emulated-device fillers,
vfio->pci-bar-stub swap, serial) that make an offline dump reproduce a real
launch's measured ACPI. The byte-exact acceptance (== box-028) runs in the
tdx-measure container, not here.
"""

import pytest

from chutes.guest.gpu.profiles import GPU_PROFILES
from chutes.guest.gpu.topology import FlatTopology, NumaTopology
from platform_tables import spec_to_metadata
from topology_spec import build_topology_spec

_FW = "/opt/ovmf/OVMF.fd"


def _md(model, fingerprint, **kw):
    profile = GPU_PROFILES[model]
    spec = build_topology_spec(
        profile, fingerprint, cpu_args="host,-avx10", firmware=_FW
    )
    return spec_to_metadata(spec, profile, acpi_tables="/out/acpi.bin", **kw)


def _rtx_numa():
    return _md("RTX_PRO_6000", NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1)))


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
    for i, stub in enumerate(sorted(stubs, key=lambda s: int(s.split("bus=rp")[1].split(",")[0])), 1):
        assert f"bus=rp{i}," in stub
        assert "bars=0:64M:p64;2:128G:p64;4:32M:p64" in stub
        assert "vendor=0x10de" in stub


def test_serial_attached_for_com1():
    q = _rtx_numa()["boot_config"]["qemu"]
    assert q["serial"] == ["null"]


def test_smbios_can_be_dropped():
    with_it = _md("RTX_PRO_6000", NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1)), with_smbios=True)
    without = _md("RTX_PRO_6000", NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1)), with_smbios=False)
    assert with_it["boot_config"]["qemu"]["smbios"]
    assert without["boot_config"]["qemu"]["smbios"] == []


def test_flat_topology_generates():
    q = _md("RTX_PRO_6000", FlatTopology(gpu_count=8))["boot_config"]["qemu"]
    assert not any("pxb-pcie" in d for d in q["devices"])
    assert sum(d.startswith("pci-bar-stub") for d in q["devices"]) == 8


def test_boot_config_scalars():
    bc = _rtx_numa()["boot_config"]
    assert bc["cpus"] == 124
    assert bc["memory"] == "768G"
    assert bc["acpi_tables"] == "/out/acpi.bin"


def test_nvswitch_endpoint_not_yet_modeled():
    # NVSwitch/IB are passthrough devices too; their BARs also shape the DSDT and
    # need their own captured layout. Until then, generation fails loudly rather
    # than silently producing a wrong measurement.
    fp = NumaTopology(
        gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1), nvswitch_nodes=(0, 1, 0, 1)
    )
    with pytest.raises(NotImplementedError, match="BAR layout"):
        _md("H200", fp)
