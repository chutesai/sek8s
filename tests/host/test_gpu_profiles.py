"""Unit tests for GPU profile registry (host-tools).

Tests focus on behavioral contracts and logic branches, not static values.
"""

import pytest
from chutes_host.gpu.profiles import (
    GPU_PROFILES,
    HOST_RESERVED_CPUS,
    GpuProfile,
    resolve_profile,
)

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
    """Each profile only matches its own PCI IDs, not another profile's."""
    for key, profile in GPU_PROFILES.items():
        other_ids = [
            pid for k, p in GPU_PROFILES.items() if k != key for pid in p.pci_device_ids
        ]
        for foreign_id in other_ids:
            assert not profile.matches_device_id(
                foreign_id
            ), f"{key} should not match {foreign_id}"


# ---------------------------------------------------------------------------
# Registry integrity: no duplicate PCI device IDs across profiles
# ---------------------------------------------------------------------------


def test_no_duplicate_pci_device_ids_across_profiles():
    seen: dict[str, str] = {}
    for key, profile in GPU_PROFILES.items():
        for pid in profile.pci_device_ids:
            pid_lower = pid.lower()
            assert pid_lower not in seen, (
                f"PCI device ID {pid} claimed by both " f"{seen[pid_lower]} and {key}"
            )
            seen[pid_lower] = key


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
    """vcpus must be host_cpus minus the shared HOST_RESERVED_CPUS constant."""
    profile = GPU_PROFILES[model_key]
    assert profile.vcpus == profile.host_cpus - HOST_RESERVED_CPUS
    assert profile.vcpus > 0


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
