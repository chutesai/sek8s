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


def document(**over):
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
            "total": 128,
            "sockets": 2,
            "cpu_vendor": "GenuineIntel",
            "cpu_processor_id": "f2060c00fffba91f",
        },
        "memory": {"total_gb": 2048.0},
        "numa": {"node_count": 2},
        "qemu": {"qemu_version": "10.2.1"},
    }
    doc.update(over)
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


def test_missing_processor_id_stays_none():
    """None is refused by offline generation rather than measured as the generating host's CPU."""
    doc = document(cpu={"total": 128, "sockets": 2, "cpu_vendor": "GenuineIntel"})
    assert HostProfile(doc).cpu.processor_id is None


def test_guest_shape_comes_from_the_profile_rule_not_host_capacity():
    profile = HostProfile(document())
    assert profile.vcpus == 124  # 128 host CPUs less H200's reserve of 4
    assert profile.guest_mem_gb == 1128  # 8 x 141 GB VRAM, NOT the host's 2048
    assert profile.smp_topology == "124,sockets=2,cores=62,threads=1"
    assert profile.mem == "1128G"


def test_guest_numa_needs_both_the_profile_and_two_host_nodes():
    assert HostProfile(document()).uses_guest_numa is True
    assert HostProfile(document(numa={"node_count": 4})).uses_guest_numa is False
    # B300 has enable_numa_topology=False, so two host nodes are not enough.
    b300 = document(gpus=[gpu(f"0000:{b}:00.0", 0, "3182") for b in ("19", "3b")])
    assert HostProfile(b300).uses_guest_numa is False


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
            "total": 256,
            "sockets": 2,
            "cpu_vendor": "GenuineIntel",
            "cpu_processor_id": "d1060a00fffba91f",
        },
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
