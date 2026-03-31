"""Unit tests for host profile registry and setup orchestration.

Tests focus on behavioral contracts, registry integrity, and setup
orchestration logic (mocking all subprocess/OS calls).
"""

from unittest.mock import MagicMock, patch

import pytest
from chutes.host.profiles import (
    HOST_PROFILES,
    PPA,
    HostProfile,
    Ubuntu2504Profile,
    Ubuntu2510Profile,
    resolve_profile,
)
from chutes.host.setup import _get_kernel_version, setup_host

# ---------------------------------------------------------------------------
# PPA dataclass
# ---------------------------------------------------------------------------


def test_ppa_uri_format():
    ppa = PPA("kobuk-team", "tdx-release", signing_key="AABBCCDD")
    assert ppa.uri == "ppa:kobuk-team/tdx-release"


def test_ppa_default_pin_priority():
    ppa = PPA("team", "name", signing_key="AABB")
    assert ppa.pin_priority == 4000


def test_ppa_custom_pin_priority():
    ppa = PPA("team", "name", signing_key="AABB", pin_priority=500)
    assert ppa.pin_priority == 500


def test_ppa_suite_defaults_to_none():
    ppa = PPA("team", "name", signing_key="AABB")
    assert ppa.suite is None


def test_ppa_suite_override():
    ppa = PPA("team", "name", signing_key="AABB", suite="oracular")
    assert ppa.suite == "oracular"


def test_ppa_signing_key_required():
    """Every PPA in every profile must declare a signing key."""
    for version, profile in HOST_PROFILES.items():
        for ppa in profile.ppas:
            assert ppa.signing_key, f"{version} PPA {ppa.name} missing signing_key"


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------


def test_all_registered_profiles_are_host_profile_subclasses():
    for key, profile in HOST_PROFILES.items():
        assert isinstance(profile, HostProfile), f"{key} is not a HostProfile"


def test_registry_keys_match_profile_names():
    for key, profile in HOST_PROFILES.items():
        assert (
            key == profile.name
        ), f"Registry key '{key}' does not match profile.name '{profile.name}'"


def test_no_duplicate_codenames():
    codenames = [p.codename for p in HOST_PROFILES.values()]
    assert len(codenames) == len(set(codenames)), "Duplicate codenames in registry"


# ---------------------------------------------------------------------------
# All profiles: common contracts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", list(HOST_PROFILES.keys()))
def test_every_profile_has_nohibernate(version):
    """nohibernate must be in every profile's GRUB cmdline."""
    profile = HOST_PROFILES[version]
    assert "nohibernate" in profile.grub_cmdline_additions


@pytest.mark.parametrize("version", list(HOST_PROFILES.keys()))
def test_every_profile_includes_attestation_packages(version):
    """Attestation is mandatory on every host -- packages must be present."""
    profile = HOST_PROFILES[version]
    required = {"sgx-dcap-pccs", "tdx-qgs", "libsgx-dcap-default-qpl"}
    assert required.issubset(
        set(profile.packages)
    ), f"{version} missing attestation packages: {required - set(profile.packages)}"


@pytest.mark.parametrize("version", list(HOST_PROFILES.keys()))
def test_every_profile_includes_qemu(version):
    profile = HOST_PROFILES[version]
    assert "qemu-system-x86" in profile.packages


@pytest.mark.parametrize("version", list(HOST_PROFILES.keys()))
def test_describe_contains_version_and_codename(version):
    profile = HOST_PROFILES[version]
    desc = profile.describe()
    assert profile.name in desc
    assert profile.codename in desc


# ---------------------------------------------------------------------------
# Ubuntu 25.04 specifics
# ---------------------------------------------------------------------------


def test_2504_needs_tdx_release_ppa():
    """25.04 doesn't have native TDX, so it needs the tdx-release PPA."""
    profile = Ubuntu2504Profile()
    ppa_names = {ppa.name for ppa in profile.ppas}
    assert "tdx-release" in ppa_names


def test_2504_uses_intel_kernel():
    profile = Ubuntu2504Profile()
    assert profile.kernel_package == "linux-image-intel"


# ---------------------------------------------------------------------------
# Ubuntu 25.10 specifics
# ---------------------------------------------------------------------------


def test_2510_does_not_need_tdx_release_ppa():
    """25.10 has native TDX kernel -- no tdx-release PPA needed."""
    profile = Ubuntu2510Profile()
    ppa_names = {ppa.name for ppa in profile.ppas}
    assert "tdx-release" not in ppa_names


def test_2510_attestation_ppa_pinned_to_oracular():
    """25.10 attestation PPA must use oracular suite (no questing packages)."""
    profile = Ubuntu2510Profile()
    attestation_ppas = [p for p in profile.ppas if "attestation" in p.name]
    assert len(attestation_ppas) == 1
    assert attestation_ppas[0].suite == "oracular"


def test_2504_ppas_use_native_suite():
    """25.04 PPAs should not override suite (packages published for plucky)."""
    profile = Ubuntu2504Profile()
    for ppa in profile.ppas:
        assert ppa.suite is None


def test_2510_uses_generic_kernel():
    profile = Ubuntu2510Profile()
    assert profile.kernel_package == "linux-image-generic"


def test_2510_enables_kvm_intel_tdx():
    """25.10 requires explicit kvm_intel.tdx=1 kernel param."""
    profile = Ubuntu2510Profile()
    assert "kvm_intel.tdx=1" in profile.grub_cmdline_additions


def test_2504_does_not_set_kvm_intel_tdx():
    """25.04 gets TDX via PPA kernel -- no kvm_intel param needed."""
    profile = Ubuntu2504Profile()
    assert "kvm_intel.tdx=1" not in profile.grub_cmdline_additions


# ---------------------------------------------------------------------------
# resolve_profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", list(HOST_PROFILES.keys()))
def test_resolve_profile_returns_correct_instance(version):
    profile = resolve_profile(version)
    assert profile is HOST_PROFILES[version]


def test_resolve_profile_rejects_unsupported_version():
    with pytest.raises(ValueError, match="Unsupported Ubuntu version"):
        resolve_profile("18.04")


@patch("chutes.host.profiles.detect_ubuntu_version", return_value="25.10")
def test_resolve_profile_auto_detects(mock_detect):
    profile = resolve_profile(None)
    assert isinstance(profile, Ubuntu2510Profile)
    mock_detect.assert_called_once()


@patch("chutes.host.profiles.detect_ubuntu_version", return_value="99.99")
def test_resolve_profile_auto_detect_unsupported(mock_detect):
    with pytest.raises(ValueError, match="Unsupported Ubuntu version"):
        resolve_profile(None)


# ---------------------------------------------------------------------------
# _get_kernel_version: regex parsing
# ---------------------------------------------------------------------------


@patch("chutes.host.setup.subprocess.run")
def test_get_kernel_version_parses_depends(mock_run):
    mock_run.return_value = MagicMock(
        stdout=(
            "Package: linux-image-generic\n"
            "Version: 6.17.0.15.16\n"
            "Depends: linux-image-6.17.0-15-generic, linux-modules-6.17.0-15-generic\n"
        )
    )
    assert _get_kernel_version("linux-image-generic") == "6.17.0-15-generic"


@patch("chutes.host.setup.subprocess.run")
def test_get_kernel_version_raises_on_no_match(mock_run):
    mock_run.return_value = MagicMock(stdout="Package: something\nVersion: 1.0\n")
    with pytest.raises(RuntimeError, match="Could not determine kernel version"):
        _get_kernel_version("linux-image-generic")


# ---------------------------------------------------------------------------
# setup_host: orchestration (all subprocess calls mocked)
# ---------------------------------------------------------------------------


@patch("chutes.host.setup._install_cli_tools")
@patch("chutes.host.setup._add_user_to_kvm")
@patch("chutes.host.setup._grub_update_cmdline")
@patch("chutes.host.setup._grub_set_kernel")
@patch("chutes.host.setup._get_kernel_version", return_value="6.17.0-15-generic")
@patch("chutes.host.setup._run")
@patch("os.geteuid", return_value=0)
def test_setup_host_calls_all_steps(
    mock_euid,
    mock_run,
    mock_kver,
    mock_grub_kernel,
    mock_grub_cmdline,
    mock_kvm,
    mock_cli,
):
    profile = Ubuntu2510Profile()
    setup_host(profile)

    mock_kver.assert_called_once_with(profile.kernel_package)
    mock_grub_kernel.assert_called_once_with("6.17.0-15-generic")
    mock_grub_cmdline.assert_called_once_with(profile.grub_cmdline_additions)
    mock_kvm.assert_called_once()
    mock_cli.assert_called_once()

    install_calls = [
        c for c in mock_run.call_args_list if len(c[0]) > 0 and "install" in c[0][0]
    ]
    assert len(install_calls) > 0, "apt install should have been called"


@patch("os.geteuid", return_value=1000)
def test_setup_host_exits_if_not_root(mock_euid):
    profile = Ubuntu2510Profile()
    with pytest.raises(SystemExit):
        setup_host(profile)
