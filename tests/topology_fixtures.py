"""Sample host topologies for tests.

Host-profile documents for the shapes we generate measurements for. These were formerly
``chutes_cvm.guest.gpu.known_topologies`` -- the in-repo baseline registry -- then a set of
TopologyFingerprint values; the API owns host classes now and both paths build from a
HostProfile, so what is left is the documents themselves.
"""

from chutes_cvm.guest.gpu.profiles import GPU_PROFILES


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
