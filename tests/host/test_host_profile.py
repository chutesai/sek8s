"""HostProfile derives the guest from the captured hardware.

Values here are the real ones from the shipped profiles, so a change to a profile's reserve or
guest-RAM rule fails these rather than passing with whatever the profile now says:

    H200  id=2335 reserved=4  numa=True  nvswitch(8)=True   vram=141
    RTX   id=2bb5 reserved=4  numa=True  nvswitch(8)=False  vram=96
    B300  id=3182 reserved=4  numa=False nvswitch(8)=False  vram=288
"""

import json

import pytest
from chutes_cvm.guest.host_profile import HostProfile

H200_BARS = [
    {"index": 0, "size_mb": 16, "kind": "p64"},
    {"index": 2, "size_mb": 262144, "kind": "p64"},
    {"index": 4, "size_mb": 32, "kind": "p64"},
]


def gpu(bdf, node, device_id="2335"):
    return {
        "bdf": bdf,
        "vendor": "10de",
        "device_id": device_id,
        "pci_class": "0302",
        "numa_node": node,
        "bars": list(H200_BARS),
    }


def nvswitch(bdf, node):
    return {
        "bdf": bdf,
        "vendor": "10de",
        "device_id": "22a3",
        "pci_class": "0680",
        "numa_node": node,
        "bars": [{"index": 0, "size_mb": 32, "kind": "m64"}],
    }


def ib(bdf, node, *, bridge=False, vf=False):
    return {
        "bdf": bdf,
        "vendor": "15b3",
        "device_id": "1021",
        "pci_class": "0207",
        "numa_node": node,
        "bars": [],
        "is_bridge_pf": bridge,
        "is_vf": vf,
    }


def document(*, guest_gb=1128, **over):
    """An 8-GPU H200 host on two NUMA nodes with four NVSwitches."""
    doc = {
        "gpus": [
            gpu(f"0000:{b}:00.0", n)
            for b, n in zip(
                ("19", "3b", "4c", "5d", "9b", "bb", "cc", "dd"),
                (0, 0, 0, 0, 1, 1, 1, 1),
            )
        ],
        "nvswitches": [nvswitch(f"0000:{b}:00.0", 1) for b in ("83", "84", "85", "86")],
        "ib_devices": [],
        "cpu": {
            "count": 128,
            "sockets": 2,
            "vendor": "GenuineIntel",
            "processor_id": "f2060c00fffba91f",
        },
        "memory": {"total_gb": 2048.0},
        "numa": {"node_count": 2},
        "qemu": {"qemu_version": "10.2.1"},
    }
    doc.update(over)
    # Documents here stand in for what from_host returns, which has already resolved guest RAM.
    doc["memory"].setdefault("guest_gb", guest_gb)
    return doc


def test_devices_are_parsed_and_held_in_bdf_order():
    """Order is significant -- it drives PXB grouping -- so it is an invariant of the list."""
    doc = document()
    doc["gpus"] = list(reversed(doc["gpus"]))
    profile = HostProfile(doc)
    assert [g.bdf for g in profile.gpus] == sorted(g["bdf"] for g in doc["gpus"])
    assert profile.gpu_numa_nodes == (0, 0, 0, 0, 1, 1, 1, 1)


def test_gpu_device_id_selects_the_profile():
    assert HostProfile(document()).gpu_profile.name == "H200"
    assert HostProfile(
        document(gpus=[gpu("0000:19:00.0", 0, "2bb5")])
    ).gpu_profile.name == ("RTX_PRO_6000")


def test_mixed_gpu_models_are_refused():
    """One passthrough endpoint describes the whole platform, so a mixed host cannot be one."""
    doc = document(
        gpus=[gpu("0000:19:00.0", 0, "2335"), gpu("0000:3b:00.0", 0, "2bb5")]
    )
    with pytest.raises(ValueError, match="expected one GPU model"):
        HostProfile(doc).gpu_profile


def test_no_gpus_is_refused():
    with pytest.raises(ValueError, match="expected one GPU model"):
        HostProfile(document(gpus=[])).gpu_profile


def test_unknown_gpu_model_is_refused():
    with pytest.raises(ValueError, match="no GPU profile matches"):
        HostProfile(document(gpus=[gpu("0000:19:00.0", 0, "dead")])).gpu_profile


def test_cpu_facts_are_grouped_and_named_for_what_they_are():
    """The wire says cpu.total; a total of what is not obvious, so from_dict renames it."""
    cpu = HostProfile(document()).cpu
    assert (cpu.count, cpu.sockets) == (128, 2)
    assert cpu.vendor == "GenuineIntel"
    assert cpu.processor_id == "f2060c00fffba91f"


def test_unreadable_processor_id_stays_none():
    """The capture always emits the key, null where CPUID leaf-1 was unreadable. None is refused
    by offline generation rather than measured as the generating host's CPU."""
    doc = document(
        cpu={"count": 128, "sockets": 2, "vendor": "GenuineIntel", "processor_id": None}
    )
    assert HostProfile(doc).cpu.processor_id is None


def test_cpu_block_missing_a_key_is_refused():
    """No defaults: a host has no 0 CPUs, so defaulting one would measure a machine nobody has."""
    for key in ("count", "sockets", "vendor", "processor_id"):
        cpu = {
            "count": 128,
            "sockets": 2,
            "vendor": "GenuineIntel",
            "processor_id": "f2060c00fffba91f",
        }
        del cpu[key]
        with pytest.raises(ValueError, match=f"cpu block missing {key}"):
            HostProfile(document(cpu=cpu)).cpu


def test_required_document_values_are_never_defaulted():
    """No defaults: every one of these is an RTMR0 input, so a default would not avoid the error,
    it would move it to a measurement that silently describes the wrong machine."""
    for block, key, attr in (
        ("memory", "total_gb", "host_mem_gb"),
        ("memory", "guest_gb", "guest_mem_gb"),
        ("numa", "node_count", "numa_node_count"),
        ("qemu", "qemu_version", "qemu_version"),
    ):
        doc = document()
        del doc[block][key]
        with pytest.raises(KeyError, match=key):
            getattr(HostProfile(doc), attr)


def test_guest_shape_comes_from_the_profile_rule_not_host_capacity():
    profile = HostProfile(document())
    assert profile.vcpus == 124  # 128 host CPUs less H200's reserve of 4
    assert profile.guest_mem_gb == 1128  # 8 x 141 GB VRAM, NOT the host's 2048
    assert profile.smp_topology == "124,sockets=2,cores=62,threads=1"
    assert profile.mem == "1128G"


def test_guest_numa_needs_exactly_two_host_nodes():
    assert HostProfile(document()).uses_guest_numa is True
    assert HostProfile(document(numa={"node_count": 4})).uses_guest_numa is False
    assert HostProfile(document(numa={"node_count": 1})).uses_guest_numa is False
    # The GPU model is not consulted: a 2-node B300 host gets guest NUMA like any other.
    b300 = document(gpus=[gpu(f"0000:{b}:00.0", n, "3182") for b, n in (("19", 0), ("3b", 1))])
    assert HostProfile(b300).uses_guest_numa is True


def test_nvswitches_attach_only_when_the_profile_says_so():
    assert len(HostProfile(document()).attached_nvswitches) == 4
    # RTX never passes NVSwitches through, even on a host that reports them.
    rtx = document(gpus=[gpu(f"0000:{b}:00.0", 0, "2bb5") for b in ("19", "3b")])
    assert HostProfile(rtx).attached_nvswitches == ()
    assert HostProfile(rtx).nvswitch_numa_nodes == ()


def test_ib_is_not_attached_by_any_current_profile():
    """No shipped profile passes IB through. Taking the raw vector anyway is the bug that built
    14 rp_ib root ports on a B300 for devices the launcher never attaches."""
    doc = document(ib_devices=[ib("0000:15:00.0", 0), ib("0000:16:00.0", 1)])
    profile = HostProfile(doc)
    assert profile.ib_devices  # captured
    assert profile.attached_ib == ()  # but not attached
    assert profile.ib_numa_nodes == ()


def test_bridge_pfs_and_vfs_are_never_attached(monkeypatch):
    """A bridge PF carries NVSwitch fabric management and must stay on the host."""
    doc = document(
        ib_devices=[
            ib("0000:15:00.0", 0),
            ib("0000:16:00.0", 1, bridge=True),
            ib("0000:17:00.1", 1, vf=True),
        ]
    )
    profile = HostProfile(doc)
    monkeypatch.setattr(
        type(profile.gpu_profile),
        "should_passthrough_infiniband",
        property(lambda self: True),
    )
    assert [d.bdf for d in profile.attached_ib] == ["0000:15:00.0"]


def test_variant_label_numa_path_carries_node_signatures():
    assert HostProfile(document()).variant_label == "numa-124c-1128g-nvsw-node1"


def test_variant_label_flat_path_carries_counts():
    """B300 disables guest NUMA, so only how many devices attach can matter."""
    doc = document(
        gpus=[gpu(f"0000:{b}:00.0", 0, "3182") for b in ("19", "3b", "4c", "5d")],
        nvswitches=[],
        cpu={
            "count": 256,
            "sockets": 2,
            "vendor": "GenuineIntel",
            "processor_id": "d1060a00fffba91f",
        },
        numa={"node_count": 4},  # flat: more nodes than the builder can express
        guest_gb=992,
    )
    profile = HostProfile(doc)
    assert profile.uses_guest_numa is False
    assert profile.variant_label == f"flat-252c-{profile.guest_mem_gb}g"


def test_nothing_derivable_is_stored():
    """Counts and id sets are projections of the device lists, never fields beside them."""
    doc = document()
    assert "count" not in doc["gpus"][0]
    assert len(HostProfile(doc).gpus) == 8


def test_round_trips_through_json():
    profile = HostProfile(document())
    again = HostProfile(json.loads(profile.to_json()))
    assert again.gpu_numa_nodes == profile.gpu_numa_nodes
    assert again.variant_label == profile.variant_label
    assert again.gpus[0].bars_arg == "0:16M:p64;2:256G:p64;4:32M:p64"


# ── to_api_profile: the subset the API stores ───────────────────────────────


def test_api_profile_carries_only_rtmr0_determinants():
    """Stored == hashed == required, one set.

    An unhashed field stored beside a hashed one re-splits the class at the byte level: two hosts
    that measure identically differ in the stored row, and the API's first-write-wins silently
    drops one. Host RAM is the worked example -- 2007 GB and 2011 GB are one H200 class.
    """
    api = HostProfile(document()).to_api_profile()
    assert set(api) == {
        "gpus",
        "nvswitches",
        "ib_devices",
        "cpu",
        "memory",
        "numa",
        "qemu",
    }
    assert set(api["cpu"]) == {"count", "sockets", "vendor", "processor_id"}
    assert set(api["memory"]) == {"guest_gb"}  # not the host total it came from
    assert set(api["numa"]) == {"node_count"}
    assert set(api["qemu"]) == {"qemu_version"}


def test_api_profile_does_not_send_host_addresses():
    """A BDF is mandatory on a capture -- it binds the device and orders the list -- but the
    measured command swaps every endpoint for a pci-bar-stub, so it never reaches RTMR0. Sending
    it would split two hosts whose only difference is which slots the cards sit in."""
    profile = HostProfile(document())
    assert all(d.bdf for d in profile.gpus)  # required on the capture
    api = profile.to_api_profile()
    assert all("bdf" not in d for d in api["gpus"])
    assert set(api["gpus"][0]) == {
        "vendor",
        "device_id",
        "pci_class",
        "numa_node",
        "bars",
    }


def test_api_profile_sends_attached_devices_not_inventory():
    """Gating happens once, here. The old path took the raw vectors and built root ports for
    devices the launcher never attaches -- 14 rp_ib on a B300 -- so the generated RTMR0 could not
    match a real boot."""
    doc = document(ib_devices=[ib(f"0000:{0x15 + i:02x}:00.0", 0) for i in range(4)])
    profile = HostProfile(doc)
    assert profile.ib_devices  # captured
    assert profile.to_api_profile()["ib_devices"] == []  # no profile passes IB through


def test_api_profile_round_trips_for_generation():
    """Generation rebuilds a HostProfile from the stored row, through from_api_profile, which
    supplies the positional stand-ins the API does not keep."""
    src = HostProfile(document())
    back = HostProfile.from_api_profile(src.to_api_profile())
    assert back.gpu_profile.name == src.gpu_profile.name
    assert back.cpu == src.cpu
    assert (back.vcpus, back.guest_mem_gb) == (src.vcpus, src.guest_mem_gb)
    assert back.variant_label == src.variant_label
    assert [d.bdf for d in back.gpus] == [
        f"{i:04x}:00:00.0" for i in range(len(back.gpus))
    ]


def test_guest_ram_is_carried_not_recomputed():
    """The rule is per-GpuProfile, so a later release deriving a different answer would measure
    one guest and file it under a key computed from another. Storage drops the host total it came
    from, which makes re-deriving impossible as well as wrong."""
    api = HostProfile(document()).to_api_profile()
    assert "total_gb" not in api["memory"]
    assert (
        HostProfile.from_api_profile(api).guest_mem_gb
        == api["memory"]["guest_gb"]
        == 1128
    )


def test_stored_profile_builds_the_command_generation_measures():
    """The stand-in addresses never reach the measurement: every endpoint is swapped for a
    pci-bar-stub keyed on its root port."""
    back = HostProfile.from_api_profile(HostProfile(document()).to_api_profile())
    cmd = back.qemu_command(firmware="/opt/ovmf/OVMF.fd", cpu_args="host,-avx10")
    vfio = [d for d in cmd.devices if d.startswith("vfio-pci")]
    assert len(vfio) == 12  # 8 GPUs + 4 NVSwitches
    assert all("bus=rp" in d for d in vfio)


def test_guest_ram_leaves_the_host_its_reserve():
    """TDX guest memory is pinned and unreclaimable, so a guest that overruns the host OOM-kills
    QEMU rather than paging. The reserve binds only when VRAM exceeds host RAM."""
    from chutes_cvm.guest.host_profile import VM_MEM_RESERVE_GB

    doc = document()
    doc["memory"]["total_gb"] = 1024  # under 8x141 GB of VRAM
    assert HostProfile(doc)._derived_guest_mem_gb == 960 == 1024 - VM_MEM_RESERVE_GB


def test_host_too_small_for_its_gpus_is_refused():
    """A guest far below its GPUs' VRAM thrashes rather than fails, so it is refused at sizing
    rather than launched slowly. 8x H200 is 1128G of VRAM; the floor is 70% of that."""
    doc = document()
    doc["memory"]["total_gb"] = 900  # backs 832G, 74% -- allowed
    assert HostProfile(doc)._derived_guest_mem_gb == 832

    doc["memory"]["total_gb"] = 800  # backs 736G, 65% -- refused
    with pytest.raises(ValueError, match="under the 70% floor"):
        HostProfile(doc)._derived_guest_mem_gb


def test_flat_topology_shape():
    """A host whose node count is not 2 gets a flat guest: one machine-level memory backend, no
    SRAT/SLIT, no PXB grouping.

    Pinned because nothing else covers it -- every class with a validated measurement is a NUMA
    class, so this path reaches production unverified.
    """
    assert HostProfile(document()).uses_guest_numa is True  # 2 nodes -> NUMA path

    flat = HostProfile(document(numa={"node_count": 4}))
    assert flat.uses_guest_numa is False
    cmd = flat.qemu_command(firmware="/opt/ovmf/OVMF.fd", cpu_args="host,-avx10")
    assert cmd.numa == []
    assert "memory-backend=mem0" in cmd.machine
    assert not any("pxb-pcie" in d for d in cmd.devices)


def test_guest_numa_is_a_cpu_fact_not_a_gpu_one():
    """Guest NUMA is vCPUs grouped into nodes with node-local memory -- a CPU/memory property.
    It depends on the host having nodes to bind to, not on what is plugged into them.

    GpuProfile.enable_numa_topology used to gate this. It recorded a host fact ("2 nodes, GPUs
    split 4+4, confirmed on <hostname>") on a GPU class, left from when GpuProfile *was* the host
    profile, and it forced two 2-node B300 hosts onto the flat path for no reason.
    """
    assert HostProfile(document()).uses_guest_numa is True

    # Same host, every GPU on one node: the vCPUs still want node-local memory.
    one_node = HostProfile(
        document(gpus=[gpu(f"0000:{0x19 + i:02x}:00.0", 0) for i in range(8)])
    )
    assert one_node.numa_node_count == 2
    assert one_node.uses_guest_numa is True

    # Four nodes: more than the builder can express (4 sockets + an NxN SLIT), so flat.
    assert HostProfile(document(numa={"node_count": 4})).uses_guest_numa is False
