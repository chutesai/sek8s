"""Sample host topologies for tests.

These were formerly ``chutes_cvm.guest.gpu.known_topologies`` — the in-repo baseline registry.
Production no longer hardcodes host classes (the API is the source of truth: measurements are
generated from the host profiles it returns), so these live here purely as fixtures to exercise
the RTMR0 / topology-spec machinery with realistic CpuTopology / TopologyFingerprint values.
"""

from chutes_cvm.guest.gpu.profiles import GPU_PROFILES
from chutes_cvm.guest.gpu.topology import (
    CpuTopology,
    FlatTopology,
    NumaTopology,
    TopologyFingerprint,
)

# ── CPU shapes ──────────────────────────────────────────────────────────────────
H200_EMERALD = CpuTopology(
    vcpus=124, sockets=2, cpu_vendor="GenuineIntel", cpu_processor_id="f2060c00fffba91f"
)
B200_XEON = CpuTopology(vcpus=176, sockets=2, cpu_vendor="GenuineIntel")
B200_XEON6 = CpuTopology(vcpus=272, sockets=2, cpu_vendor="GenuineIntel")
RTX_XEON = CpuTopology(
    vcpus=124, sockets=2, cpu_vendor="GenuineIntel", cpu_processor_id="f3060a00fffba91f"
)

# ── Full fingerprints (CpuTopology × guest RAM × GpuTopology) ────────────────────
H200_KR6288 = TopologyFingerprint(
    H200_EMERALD,
    1128,
    NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1), nvswitch_nodes=(0, 0, 0, 0)),
)
H200_XE9680 = TopologyFingerprint(
    H200_EMERALD,
    1128,
    NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1), nvswitch_nodes=(1, 1, 1, 1)),
)
B200_XEON_FP = TopologyFingerprint(
    B200_XEON, 1944, NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))
)
B200_XEON6_FP = TopologyFingerprint(
    B200_XEON6, 2952, NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))
)
RTX_NUMA = TopologyFingerprint(
    RTX_XEON, 768, NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))
)
RTX_FLAT = TopologyFingerprint(RTX_XEON, 768, FlatTopology(gpu_count=8))


# ── Host-profile documents ──────────────────────────────────────────────────────
# The measurement side now derives from a HostProfile built over device lists, not from a
# TopologyFingerprint. These build the equivalent document. The fingerprints above stay for the
# launch side, which still builds one from sysfs; they go when the launcher reads the document.


def _pci(bdf, node, vendor, device_id, pci_class, bars, **extra):
    return {
        "bdf": bdf,
        "vendor": vendor,
        "device_id": device_id,
        "pci_class": pci_class,
        "numa_node": node,
        "bars": list(bars),
        **extra,
    }


def host_document(
    model,
    *,
    vcpus,
    gpu_nodes,
    nvswitch_nodes=(),
    ib_nodes=(),
    sockets=2,
    cpu_vendor="GenuineIntel",
    cpu_processor_id="f2060c00fffba91f",
    host_mem_gb=2048,
    numa_node_count=2,
):
    """A discover-profile document for a host of ``model``.

    Expressed in the same terms as the fingerprints above -- ``vcpus`` and the per-device node
    vectors -- with the host facts they imply derived back out: ``cpu.total`` is ``vcpus`` plus
    the profile's reserve, and guest RAM follows the profile's own rule from ``host_mem_gb``.
    """
    profile = GPU_PROFILES[model]
    endpoint = profile.passthrough.get("gpu")
    bars = [
        {"index": b.index, "size_mb": b.size_mb, "kind": b.kind}
        for b in (endpoint.bars if endpoint else [])
    ]
    return {
        "gpus": [
            _pci(
                f"0000:{0x19 + i:02x}:00.0",
                n,
                "10de",
                profile.pci_device_id,
                "0302",
                bars,
            )
            for i, n in enumerate(gpu_nodes)
        ],
        "nvswitches": [
            _pci(
                f"0000:{0x83 + i:02x}:00.0",
                n,
                "10de",
                "22a3",
                "0680",
                [{"index": 0, "size_mb": 32, "kind": "m64"}],
            )
            for i, n in enumerate(nvswitch_nodes)
        ],
        "ib_devices": [
            _pci(
                f"0000:{0x15 + i:02x}:00.0",
                n,
                "15b3",
                "1021",
                "0207",
                [],
                is_bridge_pf=False,
                is_vf=False,
            )
            for i, n in enumerate(ib_nodes)
        ],
        "cpu": {
            "total": vcpus + profile.host_reserved_cpus,
            "sockets": sockets,
            "cpu_vendor": cpu_vendor,
            "cpu_processor_id": cpu_processor_id,
        },
        "memory": {"total_gb": host_mem_gb},
        "numa": {"node_count": numa_node_count},
        "qemu": {"qemu_version": "10.2.1"},
    }


def rtx_numa_doc():
    return host_document(
        "RTX_PRO_6000",
        vcpus=124,
        gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1),
        cpu_processor_id="f3060a00fffba91f",
    )


def rtx_flat_doc():
    return host_document(
        "RTX_PRO_6000",
        vcpus=124,
        gpu_nodes=(-1,) * 8,
        cpu_processor_id="f3060a00fffba91f",
        numa_node_count=4,
    )


def h200_doc(nvswitch_node=0):
    return host_document(
        "H200",
        vcpus=124,
        gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1),
        nvswitch_nodes=(nvswitch_node,) * 4,
    )
