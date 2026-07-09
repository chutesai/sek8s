"""Unit tests for GPU profile registry (host-tools).

Tests focus on behavioral contracts and logic branches, not static values.
"""

import pytest
from chutes.guest.gpu.profiles import (
    GPU_PROFILES,
    HOST_RESERVED_CPUS,
    GpuProfile,
    resolve_profile,
)
from chutes.guest.gpu.topology import FlatTopology, NumaTopology

# ---------------------------------------------------------------------------
# matches_device_id: case-insensitive matching logic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "device_id",
    ["2bb1", "2BB1", "2Bb1"],
)
def test_device_id_matching_is_case_insensitive(device_id):
    profile = GPU_PROFILES["RTX_PRO_6000"]
    assert profile.matches_device_id(device_id)


def test_device_id_rejects_other_profiles_ids():
    """Each profile should not match a foreign profile's device IDs.

    Sibling profiles (same device ID, different host SKU) are an intentional
    exception — e.g. B200 and B200_XEON6 both use 2901 and are disambiguated
    by host CPU count at runtime.
    """

    # Build sibling groups: profiles that share at least one device ID
    def _sibling_keys(key: str, profile: "GpuProfile") -> set[str]:
        our_ids = set(pid.lower() for pid in profile.pci_device_ids)
        return {
            k
            for k, p in GPU_PROFILES.items()
            if k != key and set(pid.lower() for pid in p.pci_device_ids) & our_ids
        }

    for key, profile in GPU_PROFILES.items():
        siblings = _sibling_keys(key, profile)
        non_sibling_ids = [
            pid
            for k, p in GPU_PROFILES.items()
            if k != key and k not in siblings
            for pid in p.pci_device_ids
        ]
        for foreign_id in non_sibling_ids:
            assert not profile.matches_device_id(
                foreign_id
            ), f"{key} should not match {foreign_id}"


def test_b200_variants_share_device_id():
    """B200 and B200_XEON6 are siblings — same GPU, different host CPU SKU."""
    assert (
        GPU_PROFILES["B200"].pci_device_ids == GPU_PROFILES["B200_XEON6"].pci_device_ids
    )


# ---------------------------------------------------------------------------
# Registry integrity: duplicate PCI device IDs only allowed for explicit siblings
# ---------------------------------------------------------------------------


def test_no_duplicate_pci_device_ids_across_profiles():
    """Duplicate device IDs are only allowed between intentional sibling pairs.

    Siblings (profiles that share a device ID) must differ in host_cpus so
    the runtime disambiguator can pick between them. Any other duplication is
    a registration error.
    """
    # group profiles by device ID
    by_device_id: dict[str, list[str]] = {}
    for key, profile in GPU_PROFILES.items():
        for pid in profile.pci_device_ids:
            by_device_id.setdefault(pid.lower(), []).append(key)

    for pid, keys in by_device_id.items():
        if len(keys) <= 1:
            continue
        # Multiple profiles share this ID — they must all have distinct host_cpus
        cpu_counts = [GPU_PROFILES[k].host_cpus for k in keys]
        assert len(cpu_counts) == len(set(cpu_counts)), (
            f"PCI device ID {pid} is claimed by {keys} but they have "
            f"the same host_cpus={cpu_counts}; disambiguation is impossible"
        )


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


def test_b300_skips_ovmf_mmio_fw_cfg():
    """B300: 8×512 GiB BARs need OVMF auto-sized aggregate MMIO, not per-GPU fw_cfg."""
    assert GPU_PROFILES["B300"].use_ovmf_mmio_fw_cfg is False


@pytest.mark.parametrize("model_key", ["B200", "H200", "RTX_PRO_6000"])
def test_other_profiles_use_ovmf_mmio_fw_cfg(model_key):
    assert GPU_PROFILES[model_key].use_ovmf_mmio_fw_cfg is True


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
# vCPU allocation: every profile reserves cores for the host
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_key", list(GPU_PROFILES.keys()))
def test_vcpus_reserves_cores_for_host(model_key):
    """vcpus must be host_cpus minus the profile's per-profile reserve."""
    profile = GPU_PROFILES[model_key]
    assert profile.vcpus == profile.host_cpus - profile.host_reserved_cpus
    assert profile.vcpus > 0


@pytest.mark.parametrize("model_key", list(GPU_PROFILES.keys()))
def test_host_reserved_cpus_is_even(model_key):
    """Reserve must be even so vcpus stays divisible across sockets."""
    profile = GPU_PROFILES[model_key]
    assert profile.host_reserved_cpus % 2 == 0


def test_host_reserved_cpus_default_and_b200_override():
    """Default reserve is HOST_RESERVED_CPUS; B200 family overrides to 16."""
    assert GPU_PROFILES["H200"].host_reserved_cpus == HOST_RESERVED_CPUS
    assert GPU_PROFILES["B300"].host_reserved_cpus == HOST_RESERVED_CPUS
    assert GPU_PROFILES["B200"].host_reserved_cpus == 16
    assert GPU_PROFILES["B200_XEON6"].host_reserved_cpus == 16


# ---------------------------------------------------------------------------
# SMP topology: sockets, core divisibility, format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_key", list(GPU_PROFILES.keys()))
def test_smp_topology_vcpu_count_matches_vcpus(model_key):
    """First field of smp_topology must equal vcpus."""
    profile = GPU_PROFILES[model_key]
    count = int(profile.smp_topology.split(",")[0])
    assert count == profile.vcpus


@pytest.mark.parametrize("model_key", list(GPU_PROFILES.keys()))
def test_smp_topology_vcpus_divisible_by_sockets(model_key):
    """vcpus must divide evenly across sockets so each socket has equal cores."""
    profile = GPU_PROFILES[model_key]
    assert profile.vcpus % profile.host_sockets == 0, (
        f"{model_key}: vcpus={profile.vcpus} not divisible by "
        f"host_sockets={profile.host_sockets}"
    )


@pytest.mark.parametrize("model_key", list(GPU_PROFILES.keys()))
def test_smp_topology_threads_is_one(model_key):
    """threads=1 must always be set (no guest SMT)."""
    profile = GPU_PROFILES[model_key]
    assert "threads=1" in profile.smp_topology


@pytest.mark.parametrize(
    "model_key", ["RTX_PRO_6000", "H200", "B200", "B200_XEON6", "B300"]
)
def test_two_socket_profiles_use_two_sockets(model_key):
    """2-socket servers must reflect physical socket count in smp_topology.

    A flat sockets=1 topology causes QEMU to emit a degenerate CPUID with only a
    thread level and 0-bit shift — no core or package levels — which triggers the
    kernel 'arch topology borken' warning on every vCPU at boot.
    """
    profile = GPU_PROFILES[model_key]
    assert profile.host_sockets == 2
    assert "sockets=2" in profile.smp_topology


@pytest.mark.parametrize(
    "model_key", ["RTX_PRO_6000", "H200", "B200", "B200_XEON6", "B300"]
)
def test_two_socket_profiles_preserve_full_vcpu_count(model_key):
    """Switching to sockets=2 must not reduce the vCPU count."""
    profile = GPU_PROFILES[model_key]
    count = int(profile.smp_topology.split(",")[0])
    assert count == profile.vcpus


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
# B200_XEON6: sibling profile properties and disambiguation
# ---------------------------------------------------------------------------


def test_b200_xeon6_has_correct_cpu_topology():
    profile = GPU_PROFILES["B200_XEON6"]
    assert profile.host_cpus == 288
    assert profile.host_sockets == 2
    # Inherits B200's 16-CPU host reserve (not the default HOST_RESERVED_CPUS).
    assert profile.host_reserved_cpus == 16
    assert profile.vcpus == 288 - 16
    assert "sockets=2" in profile.smp_topology
    count = int(profile.smp_topology.split(",")[0])
    assert count == profile.vcpus


def test_b200_xeon6_has_higher_ram_per_gpu_than_b200():
    """Xeon6 host has ~3 TB RAM so it can allocate more RAM per GPU."""
    assert (
        GPU_PROFILES["B200_XEON6"].ram_per_gpu_gb > GPU_PROFILES["B200"].ram_per_gpu_gb
    )


def test_b200_xeon6_inherits_cc_mode_and_no_ib_passthrough():
    profile = GPU_PROFILES["B200_XEON6"]
    args = profile.get_cc_mode_args(8)
    assert any("--set-cc-mode=on" in a for a in args[0])
    # Inherits IB-passthrough=False from B200 (removed).
    assert profile.should_passthrough_infiniband is False
    assert profile.should_passthrough_nvswitches(8) is False


def test_b200_xeon6_enables_numa_topology_and_tuning():
    profile = GPU_PROFILES["B200_XEON6"]
    assert profile.enable_numa_topology is True
    assert profile.enable_post_launch_tuning is True


def test_match_gpu_model_disambiguates_b200_by_host_cpus():
    """_match_gpu_model picks the right B200 variant based on exact host CPU count."""
    from chutes.guest.detection import _match_gpu_model

    line = "0000:0d:00.0 3D controller [0302]: NVIDIA Corporation GB100 [B200] [10de:2901] (rev a1)"
    assert _match_gpu_model(line, host_cpus=192) == "B200"
    assert _match_gpu_model(line, host_cpus=288) == "B200_XEON6"


def test_match_gpu_model_requires_cpu_count_to_disambiguate_shared_id():
    """When multiple profiles share a device ID (B200 vs B200_XEON6, both 2901),
    _match_gpu_model needs the host CPU count to select one. Without it, it must
    raise rather than return an arbitrary (possibly wrong) profile."""
    import pytest
    from chutes.guest.detection import _match_gpu_model

    line = "0000:0d:00.0 3D controller [0302]: NVIDIA Corporation GB100 [B200] [10de:2901] (rev a1)"
    with pytest.raises(ValueError, match="refusing to guess a profile"):
        _match_gpu_model(line)


def test_match_gpu_model_raises_on_unknown_b200_cpu_count():
    """An unrecognised CPU count for a shared device ID raises ValueError."""
    import pytest
    from chutes.guest.detection import _match_gpu_model

    line = "0000:0d:00.0 3D controller [0302]: NVIDIA Corporation GB100 [B200] [10de:2901] (rev a1)"
    with pytest.raises(ValueError, match="Add a new profile for this CPU topology"):
        _match_gpu_model(line, host_cpus=240)


def test_get_gpu_models_from_lspci_uses_host_cpus_for_disambiguation():
    """get_gpu_models_from_lspci auto-detects CPU topology and routes to the correct B200 variant."""
    from unittest.mock import patch

    from chutes.guest.detection import get_gpu_models_from_lspci

    fake_lspci = [
        "0000:0d:00.0 3D controller [0302]: NVIDIA [B200] [10de:2901] (rev a1)",
    ]
    with patch("chutes.guest.detection._lspci_lines", return_value=fake_lspci):
        with patch("chutes.guest.detection.detect_host_cpus", return_value=192):
            result_192 = get_gpu_models_from_lspci(["0000:0d:00.0"])
        with patch("chutes.guest.detection.detect_host_cpus", return_value=288):
            result_288 = get_gpu_models_from_lspci(["0000:0d:00.0"])

    assert result_192 == {"0000:0d:00.0": "B200"}
    assert result_288 == {"0000:0d:00.0": "B200_XEON6"}


def test_get_gpu_models_from_lspci_raises_on_unknown_b200_cpu_count():
    """An unrecognised CPU count for a shared device ID raises ValueError, not a silent mismatch."""
    from unittest.mock import patch

    import pytest
    from chutes.guest.detection import get_gpu_models_from_lspci

    fake_lspci = [
        "0000:0d:00.0 3D controller [0302]: NVIDIA [B200] [10de:2901] (rev a1)",
    ]
    with patch("chutes.guest.detection._lspci_lines", return_value=fake_lspci):
        with patch("chutes.guest.detection.detect_host_cpus", return_value=240):
            with pytest.raises(
                ValueError, match="Add a new profile for this CPU topology"
            ):
                get_gpu_models_from_lspci(["0000:0d:00.0"])


# ---------------------------------------------------------------------------
# verify_host_qemu_supported: QEMU host-readiness gate (pre-resolution)
# ---------------------------------------------------------------------------


def test_detect_qemu_version_parses_upstream_version():
    from unittest.mock import patch

    from chutes.guest import detection

    fake = type("R", (), {"stdout": "QEMU emulator version 10.2.1 (Debian 1:10.2.1+ds-1ubuntu3.1)\n"})()
    with patch("chutes.guest.detection.subprocess.run", return_value=fake):
        assert detection.detect_qemu_version() == "10.2.1"


def test_verify_host_qemu_supported_passes_when_qemu_matches_os():
    from unittest.mock import patch

    from chutes.guest.detection import SUPPORTED_QEMU_BY_OS, verify_host_qemu_supported

    os_ver, qemu_ver = next(iter(SUPPORTED_QEMU_BY_OS.items()))
    with patch("chutes.guest.detection.detect_os_version", return_value=os_ver):
        with patch("chutes.guest.detection.detect_qemu_version", return_value=qemu_ver):
            verify_host_qemu_supported()  # must not raise


def test_verify_host_qemu_supported_raises_when_qemu_mismatches_os():
    from unittest.mock import patch

    import pytest
    from chutes.guest.detection import verify_host_qemu_supported

    # 26.04 ships 10.2.1; a host on 26.04 running 10.1.0 must be flagged.
    with patch("chutes.guest.detection.detect_os_version", return_value="26.04"):
        with patch("chutes.guest.detection.detect_qemu_version", return_value="10.1.0"):
            with pytest.raises(ValueError, match=r"ships \(and we baseline\) QEMU 10\.2\.1"):
                verify_host_qemu_supported()


def test_verify_host_qemu_supported_raises_on_unsupported_os():
    from unittest.mock import patch

    import pytest
    from chutes.guest.detection import verify_host_qemu_supported

    with patch("chutes.guest.detection.detect_os_version", return_value="24.04"):
        with patch("chutes.guest.detection.detect_qemu_version", return_value="8.2.2"):
            with pytest.raises(ValueError, match=r"OS release '24.04' is not supported"):
                verify_host_qemu_supported()


def test_verify_host_qemu_supported_raises_when_qemu_undetectable():
    from unittest.mock import patch

    import pytest
    from chutes.guest.detection import verify_host_qemu_supported

    with patch("chutes.guest.detection.detect_qemu_version", return_value=None):
        with pytest.raises(ValueError, match="Could not determine the host QEMU version"):
            verify_host_qemu_supported()


# ---------------------------------------------------------------------------
# detect_profile: full topology detection
# ---------------------------------------------------------------------------


def _make_lspci_b200(bdf: str = "0000:0d:00.0") -> list[str]:
    return [f"{bdf} 3D controller [0302]: NVIDIA [B200] [10de:2901] (rev a1)"]


def _patch_detection(
    lspci_lines=None,
    host_cpus=192,
    host_sockets=2,
    numa_count=2,
    nvswitch_bdfs=None,
    ib_pf_bdfs=None,
    gpu_bdfs=None,
    fingerprint=NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1)),
):
    """Return a context manager stack that patches all detection side effects.

    ``fingerprint`` is what host_topology_fingerprint() returns; the default is
    the B200 4+4 NUMA layout (GPUs 4+4, no NVSwitch, no IB passthrough) so B200
    resolution tests pass the topology hard-match. Pass a non-baselined value to
    exercise the refusal path.
    """
    from contextlib import ExitStack
    from unittest.mock import patch

    stack = ExitStack()
    stack.enter_context(
        patch(
            "chutes.guest.detection.host_topology_fingerprint",
            return_value=fingerprint,
        )
    )
    stack.enter_context(
        patch("chutes.guest.detection._lspci_lines", return_value=lspci_lines or [])
    )
    stack.enter_context(
        patch("chutes.guest.detection.detect_host_cpus", return_value=host_cpus)
    )
    stack.enter_context(
        patch("chutes.guest.detection.detect_host_sockets", return_value=host_sockets)
    )
    stack.enter_context(
        patch("chutes.guest.detection.detect_numa_node_count", return_value=numa_count)
    )
    stack.enter_context(
        patch(
            "chutes.guest.detection.detect_nvswitches", return_value=nvswitch_bdfs or []
        )
    )
    stack.enter_context(
        patch(
            "chutes.guest.detection.detect_infiniband_pfs",
            return_value=ib_pf_bdfs or [],
        )
    )
    stack.enter_context(
        patch("chutes.guest.detection.detect_cx7_bridge_pfs", return_value=[])
    )
    bdfs = gpu_bdfs if gpu_bdfs is not None else ["0000:0d:00.0"]
    stack.enter_context(patch("chutes.guest.detection.get_gpu_bdfs", return_value=bdfs))
    return stack


def test_detect_profile_returns_correct_profile():
    from chutes.guest.detection import detect_profile

    with _patch_detection(
        lspci_lines=_make_lspci_b200(),
        host_cpus=192,
        host_sockets=2,
        ib_pf_bdfs=["0000:0e:00.0"],
    ):
        profile = detect_profile()

    assert profile is GPU_PROFILES["B200"]


def test_detect_profile_resolves_b200_xeon6_by_cpu_count():
    from chutes.guest.detection import detect_profile

    with _patch_detection(
        lspci_lines=_make_lspci_b200(),
        host_cpus=288,
        host_sockets=2,
        # XEON6 shares the no-IB B200 numa fingerprint; disambiguated by host_cpus.
        fingerprint=NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1)),
    ):
        profile = detect_profile()

    assert profile is GPU_PROFILES["B200_XEON6"]


@pytest.mark.parametrize(
    "numa_count,fingerprint",
    [
        (2, NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))),  # 2 NUMA nodes
        (4, FlatTopology(gpu_count=8)),  # 4 NUMA nodes -> flat fallback
    ],
)
def test_detect_profile_accepts_baselined_rtx_topologies(numa_count, fingerprint):
    """Both RTX Pro 6000 host shapes (2-node NUMA and 4-node flat) are baselined
    and must pass the launch-time topology hard-match."""
    from chutes.guest.detection import detect_profile

    rtx_lines = [
        f"0000:{i:02x}:00.0 3D controller [0302]: NVIDIA "
        f"[RTX PRO 6000 Blackwell Server Edition] [10de:2bb5] (rev a1)"
        for i in range(8)
    ]
    bdfs = [f"0000:{i:02x}:00.0" for i in range(8)]
    with _patch_detection(
        lspci_lines=rtx_lines,
        host_cpus=128,
        host_sockets=2,
        numa_count=numa_count,
        gpu_bdfs=bdfs,
        fingerprint=fingerprint,
    ):
        profile = detect_profile()

    assert profile is GPU_PROFILES["RTX_PRO_6000"]


def test_detect_profile_raises_on_socket_mismatch():
    import pytest
    from chutes.guest.detection import detect_profile

    with _patch_detection(
        lspci_lines=_make_lspci_b200(),
        host_cpus=192,
        host_sockets=1,  # profile expects 2
        ib_pf_bdfs=["0000:0e:00.0"],
    ):
        with pytest.raises(ValueError, match="Socket count mismatch"):
            detect_profile()


def test_detect_profile_raises_when_nvswitches_expected_but_missing():
    import pytest
    from chutes.guest.detection import detect_profile

    h200_lines = [
        f"0000:{i:02x}:00.0 3D controller [0302]: NVIDIA [H200] [10de:2335] (rev a1)"
        for i in range(8)
    ]
    bdfs = [f"0000:{i:02x}:00.0" for i in range(8)]
    with _patch_detection(
        lspci_lines=h200_lines,
        host_cpus=128,
        host_sockets=2,
        nvswitch_bdfs=[],
        gpu_bdfs=bdfs,
    ):
        with pytest.raises(ValueError, match="NVSwitch"):
            detect_profile()


def test_detect_profile_raises_when_no_gpus():
    import pytest
    from chutes.guest.detection import detect_profile

    with _patch_detection(gpu_bdfs=[]):
        with pytest.raises(ValueError, match="No GPU devices detected"):
            detect_profile()


def test_detect_profile_raises_on_unknown_cpu_count():
    import pytest
    from chutes.guest.detection import detect_profile

    with _patch_detection(
        lspci_lines=_make_lspci_b200(),
        host_cpus=240,
    ):
        with pytest.raises(ValueError, match="Add a new profile"):
            detect_profile()


# ---------------------------------------------------------------------------
# host_topology_fingerprint + topology hard-match
# ---------------------------------------------------------------------------


def test_topology_fingerprint_numa_path_includes_device_layout():
    from unittest.mock import patch

    from chutes.guest.detection import host_topology_fingerprint

    profile = GPU_PROFILES["H200"]  # enable_numa_topology = True
    with patch("chutes.guest.detection.detect_numa_node_count", return_value=2):
        with patch(
            "chutes.guest.detection._device_numa_layout",
            side_effect=[(0, 0, 0, 0, 1, 1, 1, 1), (1, 1, 1, 1), ()],
        ):
            fp = host_topology_fingerprint(profile, ["g"] * 8, ["n"] * 4, [])
    assert fp == NumaTopology(
        gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1), nvswitch_nodes=(1, 1, 1, 1)
    )


def test_topology_fingerprint_flat_when_not_two_numa_nodes():
    from unittest.mock import patch

    from chutes.guest.detection import host_topology_fingerprint

    profile = GPU_PROFILES["H200"]
    with patch("chutes.guest.detection.detect_numa_node_count", return_value=4):
        fp = host_topology_fingerprint(profile, ["g"] * 8, ["n"] * 4, [])
    assert fp == FlatTopology(gpu_count=8, nvswitch_count=4)


def test_topology_fingerprint_flat_when_profile_disables_numa():
    # B300 never uses guest NUMA topology -> flat regardless of host node count.
    from unittest.mock import patch

    from chutes.guest.detection import host_topology_fingerprint

    profile = GPU_PROFILES["B300"]
    with patch("chutes.guest.detection.detect_numa_node_count", return_value=2):
        fp = host_topology_fingerprint(profile, ["g"] * 8, [], [])
    assert fp == FlatTopology(gpu_count=8)


def test_topology_fingerprint_includes_ib_layout_on_numa_path():
    # Two B200 hosts with the same GPU/NVSwitch layout but different IB->NUMA
    # wiring must produce different fingerprints (IB VFs are passed through and
    # attach to PXB bridges by NUMA, so they move RTMR0).
    from unittest.mock import patch

    from chutes.guest.detection import host_topology_fingerprint

    profile = GPU_PROFILES["B200"]
    gpus = ["g"] * 8
    ib = ["i0", "i1", "i2", "i3"]
    with patch("chutes.guest.detection.detect_numa_node_count", return_value=2):
        with patch(
            "chutes.guest.detection._device_numa_layout",
            side_effect=[(0, 0, 0, 0, 1, 1, 1, 1), (), (0, 0, 1, 1)],
        ):
            fp = host_topology_fingerprint(profile, gpus, [], ib)
    assert fp == NumaTopology(
        gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1), ib_nodes=(0, 0, 1, 1)
    )


def test_topology_fingerprint_ib_count_on_flat_path():
    # On the flat path only device counts matter; IB count is the ib_count field.
    from unittest.mock import patch

    from chutes.guest.detection import host_topology_fingerprint

    profile = GPU_PROFILES["B200"]
    with patch("chutes.guest.detection.detect_numa_node_count", return_value=6):
        fp = host_topology_fingerprint(profile, ["g"] * 8, [], ["i"] * 4)
    assert fp == FlatTopology(gpu_count=8, ib_count=4)


def test_detect_profile_raises_on_unbaselined_topology():
    import pytest
    from chutes.guest.detection import detect_profile

    with _patch_detection(
        lspci_lines=_make_lspci_b200(),
        host_cpus=192,
        host_sockets=2,
        # not in B200 baseline (GPU->NUMA layout differs from the 4+4 split)
        fingerprint=NumaTopology(gpu_nodes=(0, 1, 0, 1, 0, 1, 0, 1)),
    ):
        with pytest.raises(ValueError, match="not baselined for profile 'B200'"):
            detect_profile()


def test_detect_profile_skips_topology_check_for_unbaselined_profile():
    # B300 has an empty baselined_topologies set -> the topology hard-match is
    # not enforced, so an arbitrary fingerprint must not refuse the launch.
    from chutes.guest.detection import detect_profile

    b300_lines = ["0000:0d:00.0 3D controller [0302]: NVIDIA [B300] [10de:3182] (rev a1)"]
    with _patch_detection(
        lspci_lines=b300_lines,
        host_cpus=192,
        host_sockets=2,
        gpu_bdfs=["0000:0d:00.0"],
        fingerprint=("anything", "goes"),
    ):
        assert detect_profile() is GPU_PROFILES["B300"]
