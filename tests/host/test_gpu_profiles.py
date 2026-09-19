"""Unit tests for GPU profile registry (host-tools).

Tests focus on behavioral contracts and logic branches, not static values.
"""

from unittest.mock import patch

import pytest
import topology_fixtures as known
from chutes_cvm.guest.detection import verify_host_qemu_supported
from chutes_cvm.guest.gpu.profiles import GPU_PROFILES, GpuProfile, resolve_profile

# ---------------------------------------------------------------------------
# Host-shape fixtures: the RTMR0-determining host facts carried on the topology
# fingerprint (vcpus/sockets/mem_gb + CPU identity), mirroring real host classes
# (see tests/topology_fixtures.py) so the detect/fingerprint tests reproduce a
# real host's shape deterministically.
# ---------------------------------------------------------------------------
_B200_XEON_SHAPE = dict(
    vcpus=176,
    sockets=2,
    cpu_vendor="GenuineIntel",
    cpu_processor_id=None,
)
_H200_SHAPE = dict(
    vcpus=124,
    sockets=2,
    cpu_vendor="GenuineIntel",
    cpu_processor_id="f2060c00fffba91f",
)
_B300_SHAPE = dict(
    vcpus=188,
    sockets=2,
    cpu_vendor="GenuineIntel",
    cpu_processor_id=None,
)
# A realistic B200 (Xeon) fingerprint (tests/topology_fixtures.py), used as the


# ---------------------------------------------------------------------------
# matches_device_id: case-insensitive matching logic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "device_id",
    ["2bb5", "2BB5", "2Bb5"],
)
def test_device_id_matching_is_case_insensitive(device_id):
    profile = GPU_PROFILES["RTX_PRO_6000"]
    assert profile.matches_device_id(device_id)


def test_device_id_rejects_other_profiles_ids():
    """Device IDs are unique per profile now (host CPU/RAM variants are
    fingerprints, not sibling profiles), so every profile must reject every
    OTHER profile's device IDs."""
    for key, profile in GPU_PROFILES.items():
        foreign_ids = [p.pci_device_id for k, p in GPU_PROFILES.items() if k != key]
        for foreign_id in foreign_ids:
            assert not profile.matches_device_id(
                foreign_id
            ), f"{key} should not match {foreign_id}"


# ---------------------------------------------------------------------------
# Registry integrity: PCI device IDs must be unique across profiles
# ---------------------------------------------------------------------------


def test_no_duplicate_pci_device_ids_across_profiles():
    """No two profiles may share a device ID.

    Host CPU/RAM variants (e.g. B200 on Xeon vs Xeon 6) are now fingerprints of
    one profile, not separate profiles, so a device ID resolves a single profile.
    Any duplication is a registration error _match_gpu_model would raise on.
    """
    by_device_id: dict[str, list[str]] = {}
    for key, profile in GPU_PROFILES.items():
        by_device_id.setdefault(profile.pci_device_id.lower(), []).append(key)

    dupes = {pid: keys for pid, keys in by_device_id.items() if len(keys) > 1}
    assert not dupes, f"device IDs claimed by multiple profiles: {dupes}"


def test_every_profile_declares_exactly_one_device_id():
    """One product per profile — the field is a string, so this guards the values.

    A profile carries far more than a BAR layout: reserved CPUs, the guest-RAM rule, NUMA
    handling, firmware, expected_gpus and the CC/PPCIe mode arguments. Two products that
    agree on all of those today can diverge later with nothing to notice, so distinct
    hardware gets a distinct profile. RTX_PRO_6000 previously covered both the Workstation
    and Server editions, and the BAR layout it carries was measured on the Server card.
    """
    for key, profile in GPU_PROFILES.items():
        assert isinstance(
            profile.pci_device_id, str
        ), f"{key}: device id must be a string"
        assert profile.pci_device_id, f"{key}: device id must not be empty"


def test_the_passthrough_stub_names_the_profile_s_own_gpu():
    """The offline stub stands in for the real card, so it must be the same device.

    It used to declare 2bb1 while the profile also matched 2bb5 and the BAR layout came
    from a 2bb5 card — the stub named a product the measurement was not taken from.
    """
    for key, profile in GPU_PROFILES.items():
        stub = profile.passthrough.get("gpu")
        if stub is None:
            continue
        assert (
            stub.device_id.lower() == profile.pci_device_id.lower()
        ), f"{key}: stub device id {stub.device_id} != profile {profile.pci_device_id}"


def test_all_registered_profiles_are_gpu_profile_subclasses():
    for key, profile in GPU_PROFILES.items():
        assert isinstance(profile, GpuProfile), f"{key} is not a GpuProfile"


# ---------------------------------------------------------------------------
# RTX Pro 6000 behavioral contracts (no NVSwitch, no PPCIe, no IB)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gpu_count", [1, 2, 4, 8])
def test_rtx_pro_6000_never_uses_ppcie(gpu_count):
    """RTX Pro 6000 has no NVSwitch fabric, so PPCIe is never applicable."""
    profile = GPU_PROFILES["RTX_PRO_6000"]
    for arg_list in profile.get_cc_mode_args(gpu_count):
        joined = " ".join(arg_list).lower()
        assert "ppcie" not in joined


@pytest.mark.parametrize("gpu_count", [1, 2, 4, 8])
def test_rtx_pro_6000_cc_mode_is_count_independent(gpu_count):
    """Unlike H200, RTX Pro 6000 CC args don't change with GPU count."""
    profile = GPU_PROFILES["RTX_PRO_6000"]
    args = profile.get_cc_mode_args(gpu_count)
    assert len(args) == 1, "Should be a single nvidia-gpu-tools invocation"
    assert "--set-cc-mode=on" in args[0]


@pytest.mark.parametrize("gpu_count", [1, 2, 4, 8])
def test_rtx_pro_6000_never_passes_through_nvswitches(gpu_count):
    profile = GPU_PROFILES["RTX_PRO_6000"]
    assert profile.should_passthrough_nvswitches(gpu_count) is False


# ---------------------------------------------------------------------------
# NUMA topology and post-launch tuning flags
# ---------------------------------------------------------------------------


def test_b200_enables_numa_topology():
    profile = GPU_PROFILES["B200"]
    assert profile.enable_numa_topology is True


def test_b200_enables_post_launch_tuning():
    profile = GPU_PROFILES["B200"]
    assert profile.enable_post_launch_tuning is True


@pytest.mark.parametrize("model_key", ["H200", "RTX_PRO_6000"])
def test_h200_and_rtx_enable_numa_topology(model_key):
    profile = GPU_PROFILES[model_key]
    assert profile.enable_numa_topology is True
    assert profile.enable_post_launch_tuning is True


def test_b300_does_not_enable_numa_topology():
    # B300 hardware topology not yet confirmed via discover-profile.sh.
    profile = GPU_PROFILES["B300"]
    assert profile.enable_numa_topology is False
    assert profile.enable_post_launch_tuning is False


# ---------------------------------------------------------------------------
# Blackwell HGX (B200 / B300): CC mode, host-side NVSwitch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_key", ["B200", "B300"])
@pytest.mark.parametrize("gpu_count", [1, 2, 4, 8])
def test_blackwell_hgx_uses_cc_mode_only(model_key, gpu_count):
    profile = GPU_PROFILES[model_key]
    args = profile.get_cc_mode_args(gpu_count)
    assert len(args) == 1
    assert "--set-cc-mode=on" in args[0]
    flat = " ".join(args[0]).lower()
    assert "ppcie" not in flat


@pytest.mark.parametrize("model_key", ["B200", "B300"])
@pytest.mark.parametrize("gpu_count", [1, 2, 4, 8])
def test_blackwell_hgx_never_passes_through_nvswitches(model_key, gpu_count):
    profile = GPU_PROFILES[model_key]
    assert profile.should_passthrough_nvswitches(gpu_count) is False


@pytest.mark.parametrize("model_key", ["B200", "B300", "RTX_PRO_6000"])
def test_cc_mode_profiles_use_cc_sbr_reset(model_key):
    profile = GPU_PROFILES[model_key]
    args = profile.get_sbr_reset_args()
    assert args == ["--reset-with-sbr", "--reset-after-cc-mode-switch"]


def test_h200_uses_ppcie_sbr_reset():
    profile = GPU_PROFILES["H200"]
    args = profile.get_sbr_reset_args()
    assert args == ["--reset-with-sbr", "--reset-after-ppcie-mode-switch"]


def test_b200_does_not_pass_through_infiniband():
    """IB passthrough removed: it added no value and varied RTMR0 per NIC loadout;
    guest networking is virtio-net (matching H200/B300)."""
    assert GPU_PROFILES["B200"].should_passthrough_infiniband is False


def test_b300_does_not_pass_through_infiniband():
    """B300 HGX: all IB-class CX7 PFs are NVSwitch bridges; guest uses virtio-net."""
    assert GPU_PROFILES["B300"].should_passthrough_infiniband is False


def test_b300_matches_pci_device_id_3182():
    profile = GPU_PROFILES["B300"]
    assert profile.matches_device_id("3182")
    assert profile.matches_device_id("3182".upper())


# ---------------------------------------------------------------------------
# H200 conditional logic: PPCIe vs CC depends on GPU count
# ---------------------------------------------------------------------------


def test_h200_switches_to_ppcie_at_8_gpus():
    """H200 uses PPCIe mode (NVSwitch fabric) when all 8 GPUs are present."""
    profile = GPU_PROFILES["H200"]
    args = profile.get_cc_mode_args(8)
    flat = [a for invocation in args for a in invocation]
    assert "--set-ppcie-mode=on" in flat
    assert "--set-cc-mode=off" in flat
    assert profile.should_passthrough_nvswitches(8) is True


def test_h200_uses_cc_mode_below_8_gpus():
    """H200 falls back to CC mode (no NVSwitch) for partial GPU configs."""
    profile = GPU_PROFILES["H200"]
    args = profile.get_cc_mode_args(4)
    flat = [a for invocation in args for a in invocation]
    assert "--set-cc-mode=on" in flat
    assert "--set-ppcie-mode=off" in flat
    assert profile.should_passthrough_nvswitches(4) is False


# ---------------------------------------------------------------------------
# -smp shape. These assert the guest CPU topology the sample hosts produce
# (tests/topology_fixtures.py), which is what reaches the guest's MADT/SRAT.
# ---------------------------------------------------------------------------


def _sample_hosts():
    """(name, HostProfile) for each sample host document."""
    from chutes_cvm.guest.host_profile import HostProfile

    return [
        ("h200-nvsw0", HostProfile(known.h200_doc())),
        ("h200-nvsw1", HostProfile(known.h200_doc(nvswitch_node=1))),
        ("rtx-numa", HostProfile(known.rtx_numa_doc())),
        ("rtx-flat", HostProfile(known.rtx_flat_doc())),
    ]


def test_some_hosts_are_sampled():
    """Guard: the parametrized tests below must not silently no-op."""
    assert _sample_hosts()


@pytest.mark.parametrize("name,host", _sample_hosts())
def test_vcpus_positive_and_matches_smp(name, host):
    """The first -smp field must equal vcpus, and vcpus must be positive."""
    assert host.vcpus > 0
    assert int(host.smp_topology.split(",")[0]) == host.vcpus


@pytest.mark.parametrize("name,host", _sample_hosts())
def test_uses_two_sockets(name, host):
    """2-socket servers must reflect the physical socket count in -smp.

    A flat sockets=1 topology causes QEMU to emit a degenerate CPUID with only a
    thread level and 0-bit shift — no core or package levels — which triggers the
    kernel 'arch topology borken' warning on every vCPU at boot.
    """
    assert host.cpu.sockets == 2
    assert "sockets=2" in host.smp_topology


@pytest.mark.parametrize("name,host", _sample_hosts())
def test_vcpus_divisible_by_sockets(name, host):
    """vcpus must divide evenly across sockets so each socket has equal cores."""
    assert (
        host.vcpus % host.cpu.sockets == 0
    ), f"{name}: vcpus={host.vcpus} not divisible by sockets={host.cpu.sockets}"


@pytest.mark.parametrize("name,host", _sample_hosts())
def test_threads_is_one(name, host):
    """threads=1 must always be set (no guest SMT)."""
    assert "threads=1" in host.smp_topology


# ---------------------------------------------------------------------------
# resolve_profile: resolution logic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_key", list(GPU_PROFILES.keys()))
def test_resolve_profile_returns_correct_type(model_key):
    models = {"0000:41:00.0": model_key}
    profile = resolve_profile(models)
    assert profile is GPU_PROFILES[model_key]


def test_resolve_profile_with_multiple_identical_gpus():
    models = {f"0000:4{i}:00.0": "RTX_PRO_6000" for i in range(8)}
    profile = resolve_profile(models)
    assert profile is GPU_PROFILES["RTX_PRO_6000"]


def test_resolve_profile_filters_out_default_entries():
    """'default' entries (unrecognized GPUs) are ignored if a real model exists."""
    models = {
        "0000:41:00.0": "B200",
        "0000:42:00.0": "default",
        "0000:43:00.0": "default",
    }
    profile = resolve_profile(models)
    assert profile is GPU_PROFILES["B200"]


def test_resolve_profile_rejects_mixed_models():
    models = {
        "0000:41:00.0": "B200",
        "0000:42:00.0": "H200",
    }
    with pytest.raises(ValueError, match="Mixed GPU models"):
        resolve_profile(models)


def test_resolve_profile_rejects_unsupported_model():
    models = {"0000:41:00.0": "TITAN_V"}
    with pytest.raises(ValueError, match="Unsupported GPU model"):
        resolve_profile(models)


def test_resolve_profile_rejects_all_default():
    """If every GPU is 'default' (unrecognized), resolution must fail."""
    models = {"0000:41:00.0": "default", "0000:42:00.0": "default"}
    with pytest.raises(ValueError, match="No supported GPU models"):
        resolve_profile(models)


# ---------------------------------------------------------------------------
# _match_gpu_model: device ID -> profile (device IDs are unique now)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# verify_host_qemu_supported: QEMU host-readiness gate (pre-resolution)
# ---------------------------------------------------------------------------


def test_detect_qemu_version_parses_upstream_version():
    from chutes_cvm.guest import detection

    fake = type(
        "R",
        (),
        {"stdout": "QEMU emulator version 10.2.1 (Debian 1:10.2.1+ds-1ubuntu3.1)\n"},
    )()
    with patch("chutes_cvm.guest.detection.proc.run", return_value=fake):
        assert detection.detect_qemu_version() == "10.2.1"


def test_verify_host_qemu_supported_passes_when_qemu_matches_os():
    from chutes_cvm.guest.detection import (
        SUPPORTED_QEMU_BY_OS,
        verify_host_qemu_supported,
    )

    os_ver, qemu_ver = next(iter(SUPPORTED_QEMU_BY_OS.items()))
    with patch("chutes_cvm.guest.detection.detect_os_version", return_value=os_ver):
        with patch(
            "chutes_cvm.guest.detection.detect_qemu_version", return_value=qemu_ver
        ):
            verify_host_qemu_supported()  # must not raise


def test_verify_host_qemu_supported_raises_when_qemu_mismatches_os():

    # 26.04 ships 10.2.1; a host on 26.04 running 10.1.0 must be flagged.
    with patch("chutes_cvm.guest.detection.detect_os_version", return_value="26.04"):
        with patch(
            "chutes_cvm.guest.detection.detect_qemu_version", return_value="10.1.0"
        ):
            with pytest.raises(
                ValueError, match=r"ships \(and we baseline\) QEMU 10\.2\.1"
            ):
                verify_host_qemu_supported()


def test_verify_host_qemu_supported_raises_on_unsupported_os():

    with patch("chutes_cvm.guest.detection.detect_os_version", return_value="24.04"):
        with patch(
            "chutes_cvm.guest.detection.detect_qemu_version", return_value="8.2.2"
        ):
            with pytest.raises(
                ValueError, match=r"OS release '24.04' is not supported"
            ):
                verify_host_qemu_supported()


def test_verify_host_qemu_supported_raises_when_qemu_undetectable():

    with patch("chutes_cvm.guest.detection.detect_qemu_version", return_value=None):
        with pytest.raises(
            ValueError, match="Could not determine the host QEMU version"
        ):
            verify_host_qemu_supported()


# ---------------------------------------------------------------------------
# HostProfile: the launch-side guard that used to live in detect_profile
# ---------------------------------------------------------------------------


def test_nvswitch_requiring_profile_refuses_a_host_with_none():
    """H200 passes NVSwitches through, so a host reporting none cannot launch as one.

    Launching without them would build a different PCI topology than the class was measured
    for, so the guest could not attest. This guard moved here when the launcher stopped
    reading the host a second time.
    """
    import topology_fixtures as tf
    from chutes_cvm.guest.host_profile import HostProfile

    doc = tf.h200_doc()
    doc["nvswitches"] = []
    with pytest.raises(ValueError, match="requires NVSwitches"):
        HostProfile(doc).attached_nvswitches


@pytest.mark.parametrize("key", ["RTX_PRO_6000", "H200"])
def test_pci_bars_have_a_dominant_vram_aperture(key):
    """The VRAM BAR dwarfs the others, and it is what sizes the guest's 64-bit MMIO window.

    OVMF auto-sizes that window from the BARs it enumerates, so this is the one that matters.
    """
    bars = GPU_PROFILES[key].passthrough["gpu"].bars
    assert bars, f"{key} should model passthrough['gpu']"
    vram = max(bars, key=lambda b: b.size_mb)
    assert vram.size_mb >= 64 * 1024
    assert all(b.size_mb * 64 <= vram.size_mb for b in bars if b is not vram)


@pytest.mark.parametrize("key", ["RTX_PRO_6000", "H200"])
def test_pci_bars_are_well_formed(key):
    for bar in GPU_PROFILES[key].passthrough["gpu"].bars:
        assert 0 <= bar.index <= 5
        assert bar.kind in ("m32", "m64", "p32", "p64")
        # A 64-bit BAR consumes two slots, so it lands on an even index.
        if bar.kind.endswith("64"):
            assert bar.index % 2 == 0


def test_pci_bars_default_empty_when_uncaptured():
    # Profiles without an lspci capture yet model no GPU endpoint (offline
    # measurement generation is simply unavailable for them, not broken).
    assert "gpu" not in GPU_PROFILES["B300"].passthrough


# ---------------------------------------------------------------------------
# B300 on a host that cannot back aggregate VRAM (2 TB class, e.g. Wistron XD690)
# ---------------------------------------------------------------------------


def _guest_ram(model, host_gb, gpus=8):
    """Guest RAM this host would be given, via the one rule on HostProfile."""
    doc = known.host_document(
        model, vcpus=124, gpu_nodes=(0,) * gpus, host_mem_gb=host_gb
    )
    return doc["memory"]["guest_gb"]


def test_guest_ram_targets_vram_and_the_host_is_only_a_ceiling():
    """One rule for every profile: as close to aggregate VRAM as the host can back.

    B300's 8x288 GB implies a 2304G guest, which a ~2 TB sled cannot back -- run-td aborted with
    "needs 2304G guest RAM, but only 1946G can be safely backed". Host RAM is never a target in
    its own right: a bigger host does not get a bigger guest once VRAM is met.
    """
    full = GPU_PROFILES["B300"].vram_gb * 8
    assert _guest_ram("B300", 2010) == 1944  # clamped: VRAM exceeds the sled
    assert _guest_ram("B300", 2400) == full  # fits, so VRAM exactly
    assert _guest_ram("B300", 3000) == full  # bigger host, same guest


def test_guest_ram_stays_divisible_per_gpu():
    """Keeps vcpu/mem socket-divisible."""
    assert _guest_ram("B300", 2010) % 8 == 0


def test_only_a_clamped_profile_is_sensitive_to_host_ram():
    """Reaching VRAM makes a profile immune to host-RAM variance: every host of the class gets
    the same guest, so same-tier hosts cannot split into separate measurements.

    B300 is the exception, and unavoidably so -- its 8x288 GB exceeds what a 2 TB sled can back,
    so guest RAM tracks the host and two sleds 4 GB apart still measure differently.
    """
    for model in ("H200", "RTX_PRO_6000"):
        assert _guest_ram(model, 2007) == _guest_ram(model, 2011)
    assert _guest_ram("B300", 2007) != _guest_ram("B300", 2011)


def test_b300_vcpus_derive_from_a_256_cpu_host():
    """256-CPU B300 sleds need no new profile: vcpus come from the live host."""
    profile = GPU_PROFILES["B300"]
    assert 256 - profile.host_reserved_cpus == 252
