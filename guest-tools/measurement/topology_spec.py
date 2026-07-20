"""Determine the QEMU machine spec for a supported topology, offline.

The measurement side's input-determination: given a ``GpuProfile`` and a topology
fingerprint (``chutes.guest.gpu.topology``), produce the ``MachineSpec`` that
``chutes.guest.command.build_qemu_command`` turns into the exact QEMU command a
matching host would launch — with no live hardware. The launcher resolves the
same spec from live detection, so both yield a byte-identical command for a given
topology (see the parity test).

Imports the shared VM lib from the launcher package (``chutes.guest``); callers
must have ``host-tools/scripts`` on ``sys.path`` (the measurement entrypoints and
tests/measurement/conftest.py arrange this).
"""

from chutes.guest.command import DeviceSpec, MachineSpec
from chutes.guest.gpu.profiles import GpuProfile
from chutes.guest.gpu.topology import NumaTopology, TopologyFingerprint

# QEMU version -> guest -cpu string (mirrors chutes.guest.__main__: "host" on
# 24.04, else "host,-avx10"). 10.1.0 = 25.10, 10.2.1 = 26.04.
_CPU_ARGS_BY_QEMU = {"10.1.0": "host,-avx10", "10.2.1": "host,-avx10"}

# Offline measurement has no real GPU to pass through, but the launch command is
# built the same way (a vfio-pci endpoint per root port). We hand every device
# this placeholder BDF so the shared builder stays identical to the launch path;
# the measurement metadata layer then swaps each vfio-pci endpoint for a
# pci-bar-stub (matched by its ``bus=<rp_id>``), which is what reproduces the
# device's MMIO windows in the measured ACPI without the hardware present.
_PLACEHOLDER_BDF = "0000:00:00.0"


def cpu_args_for_qemu_version(qemu_version: str) -> str:
    """The guest -cpu args for a QEMU version. Defaults to the 25.10+/-avx10 form."""
    return _CPU_ARGS_BY_QEMU.get(qemu_version, "host,-avx10")


def build_topology_spec(
    profile: GpuProfile,
    fingerprint: TopologyFingerprint,
    *,
    cpu_args: str,
    firmware: str,
) -> MachineSpec:
    """Build the ``MachineSpec`` for ``(profile, fingerprint)`` — no live host.

    A ``NumaTopology`` reproduces the guest-NUMA / PXB-PCIe path (per-device node
    from the fingerprint's vectors); a ``FlatTopology`` reproduces the flat path
    (only device counts matter). ``mem`` and ``-smp`` come from the profile; no
    ``host_bdf`` is set, so only root ports are emitted (the vfio endpoints are
    not part of the measured ACPI).
    """
    numa = isinstance(fingerprint, NumaTopology)
    if numa:
        gpu_nodes: list[int] = list(fingerprint.gpu_nodes)
        nvsw_nodes: list[int] = list(fingerprint.nvswitch_nodes)
        ib_nodes: list[int] = list(fingerprint.ib_nodes)
    else:  # FlatTopology — node is irrelevant, only counts matter
        gpu_nodes = [-1] * fingerprint.gpu_count
        nvsw_nodes = [-1] * fingerprint.nvswitch_count
        ib_nodes = [-1] * fingerprint.ib_count

    gpu_count = len(gpu_nodes)
    devices: list[DeviceSpec] = []
    for i, node in enumerate(gpu_nodes):
        bar: dict = {}
        if profile.use_ovmf_mmio_fw_cfg:
            bar = {"bar_size_mb": profile.bar_size_mb, "bar_index": i + 1}
        devices.append(
            DeviceSpec(
                rp_id=f"rp{i + 1}",
                chassis=i + 1,
                host_bdf=_PLACEHOLDER_BDF,
                numa_node=node,
                **bar,
            )
        )
    for j, node in enumerate(nvsw_nodes):
        devices.append(
            DeviceSpec(
                rp_id=f"rp_nvsw{j + 1}",
                chassis=gpu_count + j + 1,
                host_bdf=_PLACEHOLDER_BDF,
                numa_node=node,
            )
        )
    for k, node in enumerate(ib_nodes):
        devices.append(
            DeviceSpec(
                rp_id=f"rp_ib{k + 1}",
                chassis=gpu_count + len(nvsw_nodes) + k + 1,
                host_bdf=_PLACEHOLDER_BDF,
                numa_node=node,
            )
        )

    return MachineSpec(
        mem=f"{gpu_count * profile.ram_per_gpu_gb}G",
        smp_topology=profile.smp_topology,
        cpu_args=cpu_args,
        firmware=firmware,
        host_nodes=[0, 1] if numa else [],
        devices=devices,
        process_name="chutes-measure",
    )
