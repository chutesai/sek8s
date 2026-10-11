"""Unit tests for host recipe registry and setup orchestration.

Tests focus on behavioral contracts, registry integrity, and setup
orchestration logic (mocking all subprocess/OS calls).
"""

from unittest.mock import MagicMock, patch

import pytest
from chutes_cvm.guest.tee import SnpTeeProvider, TdxTeeProvider
from chutes_cvm.host.recipes import (
    HOST_RECIPES,
    PPA,
    RECIPES,
    HostRecipe,
    SnpUbuntu2604Recipe,
    TdxUbuntu2604Recipe,
    Ubuntu2604Recipe,
    resolve_recipe,
)
from chutes_cvm.host.setup import (
    _ensure_chutes_dirs,
    _get_kernel_version,
    _setup_ntp,
    setup_host,
)

TDX = TdxTeeProvider()
SNP = SnpTeeProvider()


def _ID(recipe):
    return f"{recipe.tee.__name__}-{recipe.name}"


def _for(tee=None, version="26.04"):
    """Registered recipes for one platform (or all) on one version."""
    return [r for r in RECIPES if r.name == version and (tee is None or r.tee is tee)]


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
    """Every PPA in every recipe must declare a signing key."""
    for recipe in RECIPES:
        for ppa in recipe.ppas:
            assert (
                ppa.signing_key
            ), f"{recipe.describe()} PPA {ppa.name} missing signing_key"


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------


def test_all_registered_recipes_are_host_recipes():
    for recipe in RECIPES:
        assert isinstance(recipe, HostRecipe), f"{recipe!r} is not a HostRecipe"


def test_registry_is_keyed_by_version_and_platform():
    assert HOST_RECIPES == {(r.name, r.tee): r for r in RECIPES}
    assert len(HOST_RECIPES) == len(RECIPES), "two recipes for one (version, platform)"


def test_2604_has_a_recipe_for_each_platform():
    assert isinstance(HOST_RECIPES[("26.04", TdxTeeProvider)], TdxUbuntu2604Recipe)
    assert isinstance(HOST_RECIPES[("26.04", SnpTeeProvider)], SnpUbuntu2604Recipe)


def test_an_os_recipe_is_abstract_until_a_platform_is_chosen():
    with pytest.raises(TypeError):
        Ubuntu2604Recipe()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# All recipes: common contracts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe", RECIPES, ids=_ID)
def test_every_recipe_gets_the_shared_host(recipe):
    """What the OS recipe declares reaches every platform built on it."""
    assert "qemu-system-x86" in recipe.packages
    assert {"nohibernate", "modprobe.blacklist=nouveau"} <= set(
        recipe.grub_cmdline_additions
    )
    assert "nvidia-cuda" in {r.name for r in recipe.repos}


@pytest.mark.parametrize("recipe", RECIPES, ids=_ID)
def test_describe_names_platform_version_and_codename(recipe):
    desc = recipe.describe()
    assert recipe.tee.label in desc
    assert recipe.name in desc
    assert recipe.codename in desc


@pytest.mark.parametrize("recipe", RECIPES, ids=_ID)
def test_host_recipes_do_not_include_libvirt(recipe):
    """libvirt is not needed — VFIO prep uses direct PCI remove+rescan."""
    assert "libvirt-daemon-system" not in recipe.packages
    assert "libvirt-clients" not in recipe.packages


@pytest.mark.parametrize("recipe", RECIPES, ids=_ID)
def test_every_recipe_has_no_kobuk_ppas(recipe):
    """No recipe should reference kobuk-team PPAs (unreliable, superseded by Intel DCAP)."""
    assert [p for p in recipe.ppas if "kobuk" in p.team] == []


# ---------------------------------------------------------------------------
# Per platform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe", _for(TdxTeeProvider), ids=_ID)
def test_every_tdx_recipe_gets_the_attestation_stack(recipe):
    """TDX quotes are made on the host, so its quoting stack is mandatory there."""
    required = {"sgx-dcap-pccs", "tdx-qgs", "libsgx-dcap-default-qpl"}
    assert required <= set(recipe.packages)
    intel_repos = [r for r in recipe.repos if r.name == "intel-sgx"]
    assert len(intel_repos) == 1
    assert "download.01.org" in intel_repos[0].uri
    assert intel_repos[0].components == "main"
    assert intel_repos[0].signing_key_url.endswith("intel-sgx-deb.key")
    assert "kvm_intel.tdx=1" in recipe.grub_cmdline_additions


@pytest.mark.parametrize("recipe", _for(SnpTeeProvider), ids=_ID)
def test_an_snp_recipe_gets_nothing_intel(recipe):
    """SNP reports come from the PSP inside the guest: no SGX repo, DCAP stack or TDX kernel
    parameter belongs on an AMD host."""
    assert not any(p.startswith(("sgx-", "libsgx-", "tdx-")) for p in recipe.packages)
    assert "ovmf-inteltdx" not in recipe.packages
    assert "intel-sgx" not in {r.name for r in recipe.repos}
    assert not any("tdx" in p for p in recipe.grub_cmdline_additions)


@patch("chutes_cvm.host.recipes.tdx_attestation.configure")
def test_only_tdx_configures_host_attestation(mock_configure, capsys):
    TdxUbuntu2604Recipe().configure_attestation(noninteractive=True)
    mock_configure.assert_called_once_with(True)

    mock_configure.reset_mock()
    SnpUbuntu2604Recipe().configure_attestation(noninteractive=True)
    mock_configure.assert_not_called()
    assert "None on AMD SEV-SNP" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Ubuntu 26.04 specifics
# ---------------------------------------------------------------------------


def test_2604_tdx_host_is_unchanged_by_the_platform_split():
    """Splitting the recipe per platform moved nothing for an Intel host."""
    recipe = TdxUbuntu2604Recipe()
    assert recipe.packages == [
        "qemu-system-x86",
        "ovmf-inteltdx",
        "sgx-dcap-pccs",
        "tdx-qgs",
        "libsgx-dcap-default-qpl",
        "sgx-ra-service",
        "sgx-pck-id-retrieval-tool",
    ]
    assert set(recipe.grub_cmdline_additions) == {
        "nohibernate",
        "kvm_intel.tdx=1",
        "modprobe.blacklist=nouveau",
    }
    assert {r.name for r in recipe.repos} == {"intel-sgx", "nvidia-cuda"}


def test_2604_snp_host_is_only_the_os():
    recipe = SnpUbuntu2604Recipe()
    assert recipe.packages == ["qemu-system-x86"]
    assert recipe.grub_cmdline_additions == [
        "nohibernate",
        "modprobe.blacklist=nouveau",
    ]
    assert [r.name for r in recipe.repos] == ["nvidia-cuda"]


def test_2604_does_not_need_tdx_release_ppa():
    """26.04 has native TDX kernel/QEMU -- no tdx-release PPA needed."""
    assert "tdx-release" not in {ppa.name for ppa in TdxUbuntu2604Recipe().ppas}


def test_2604_tdx_uses_the_resolute_intel_repo():
    intel_repos = [r for r in TdxUbuntu2604Recipe().repos if r.name == "intel-sgx"]
    assert intel_repos[0].suite == "resolute"


@pytest.mark.parametrize("recipe", _for(), ids=_ID)
def test_2604_pins_kernel_package(recipe):
    assert recipe.kernel_package == "linux-image-7.0.0-31-generic"


# ---------------------------------------------------------------------------
# resolve_recipe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe", RECIPES, ids=_ID)
def test_resolve_recipe_returns_the_platforms_recipe(recipe):
    tee = TDX if recipe.tee is TdxTeeProvider else SNP
    assert resolve_recipe(tee, recipe.name) is recipe


@pytest.mark.parametrize("version", ["18.04", "25.10"])
def test_resolve_recipe_rejects_unsupported_version(version):
    # 26.04 is the only supported host OS; older hosts must upgrade first.
    with pytest.raises(
        ValueError, match=f"Unsupported host: Intel TDX on Ubuntu {version}"
    ):
        resolve_recipe(TDX, version)


@patch("chutes_cvm.host.recipes.detect_ubuntu_version", return_value="26.04")
def test_resolve_recipe_auto_detects(mock_detect):
    assert isinstance(resolve_recipe(SNP), SnpUbuntu2604Recipe)
    mock_detect.assert_called_once()


@patch("chutes_cvm.host.recipes.detect_ubuntu_version", return_value="99.99")
def test_resolve_recipe_auto_detect_unsupported(mock_detect):
    with pytest.raises(ValueError, match="Unsupported host"):
        resolve_recipe(TDX)


# ---------------------------------------------------------------------------
# _get_kernel_version: regex parsing
# ---------------------------------------------------------------------------


def test_get_kernel_version_parses_pinned_package():
    assert _get_kernel_version("linux-image-6.17.0-35-generic") == "6.17.0-35-generic"


def test_get_kernel_version_rejects_metapackage():
    with pytest.raises(ValueError, match="must be a pinned version"):
        _get_kernel_version("linux-image-generic")


# ---------------------------------------------------------------------------
# setup_host: orchestration (all subprocess calls mocked)
# ---------------------------------------------------------------------------


@patch("chutes_cvm.host.recipes.tdx_attestation.configure")
@patch("chutes_cvm.host.setup.write_system_file")
@patch("chutes_cvm.host.setup._ensure_chutes_dirs")
@patch("chutes_cvm.host.setup._setup_ntp")
@patch("chutes_cvm.host.setup._add_user_to_kvm")
@patch("chutes_cvm.host.setup._grub_update_cmdline")
@patch("chutes_cvm.host.setup._grub_set_kernel")
@patch("chutes_cvm.host.setup._get_kernel_version", return_value="6.17.0-15-generic")
@patch("chutes_cvm.host.setup.run")
@patch("os.geteuid", return_value=0)
def test_setup_host_calls_all_steps(
    mock_euid,
    mock_run,
    mock_kver,
    mock_grub_kernel,
    mock_grub_cmdline,
    mock_kvm,
    mock_ntp,
    mock_dirs,
    mock_write,
    mock_attestation,
):
    recipe = TdxUbuntu2604Recipe()
    setup_host(recipe)

    mock_kver.assert_called_once_with(recipe.kernel_package)
    mock_grub_kernel.assert_called_once_with("6.17.0-15-generic")
    mock_grub_cmdline.assert_called_once_with(recipe.grub_cmdline_additions)
    mock_kvm.assert_called_once()
    # The folded-in per-host config steps run as part of setup-host.
    mock_ntp.assert_called_once()
    mock_dirs.assert_called_once()

    install_calls = [
        c for c in mock_run.call_args_list if len(c[0]) > 0 and "install" in c[0][0]
    ]
    assert len(install_calls) > 0, "apt install should have been called"
    # base_packages (chrony/aria2/xfsprogs) are installed alongside the kernel + TDX stack.
    installed = [pkg for c in install_calls for pkg in c[0][0]]
    assert "chrony" in installed and "aria2" in installed and "xfsprogs" in installed


@patch("os.geteuid", return_value=1000)
def test_setup_host_exits_if_not_root(mock_euid):
    with pytest.raises(SystemExit):
        setup_host(TdxUbuntu2604Recipe())


# ---------------------------------------------------------------------------
# base_packages + folded-in host config (ntp / chutes_dirs)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", list(HOST_RECIPES.keys()))
def test_every_profile_base_packages_include_host_deps(version):
    """The folded-in host operational deps must be present so setup-host fully provisions."""
    recipe = HOST_RECIPES[version]
    assert {"chrony", "aria2", "xfsprogs"}.issubset(set(recipe.base_packages))


@patch("chutes_cvm.host.setup.run")
@patch("chutes_cvm.host.setup.write_system_file")
@patch("chutes_cvm.host.setup.proc.run", return_value=MagicMock(returncode=0))
def test_setup_ntp_masks_timesyncd_writes_conf_and_enables_chrony(
    mock_sub, mock_write, mock_run
):
    _setup_ntp()
    # systemd-timesyncd is masked (chrony owns the clock).
    assert any(
        "systemd-timesyncd" in c.args[0] and "mask" in c.args[0]
        for c in mock_sub.call_args_list
    )
    # chrony.conf is written with makestep (immediate step, not slew).
    assert mock_write.call_args.args[0] == "/etc/chrony/chrony.conf"
    assert "makestep" in mock_write.call_args.args[1]
    # chrony is enabled + started.
    assert any(
        "chrony" in c.args[0] and "enable" in c.args[0] for c in mock_run.call_args_list
    )


@patch("chutes_cvm.host.setup.proc.run", return_value=MagicMock(returncode=1))
@patch("chutes_cvm.host.setup.write_system_file")
@patch("chutes_cvm.host.setup.run")
def test_setup_ntp_tolerates_waitsync_failure(mock_run, mock_write, mock_sub):
    # A non-zero waitsync (clock not yet synced) must not raise — setup continues.
    _setup_ntp()  # returncode=1 on the tolerant subprocess calls; no exception


@patch("chutes_cvm.host.setup.os.chmod")
@patch("chutes_cvm.host.setup.os.makedirs")
def test_ensure_chutes_dirs_creates_expected(mock_makedirs, mock_chmod):
    _ensure_chutes_dirs()
    made = [c.args[0] for c in mock_makedirs.call_args_list]
    assert "/var/lib/chutes/base-images" in made
    assert "/var/lib/chutes/vm-images" in made
    # created idempotently
    assert all(c.kwargs.get("exist_ok") for c in mock_makedirs.call_args_list)


# ---------------------------------------------------------------------------
# setup_host per platform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe", RECIPES, ids=_ID)
@patch("chutes_cvm.host.recipes.tdx_attestation.configure")
@patch("chutes_cvm.host.setup.write_system_file")
@patch("chutes_cvm.host.setup._ensure_chutes_dirs")
@patch("chutes_cvm.host.setup._setup_ntp")
@patch("chutes_cvm.host.setup._add_user_to_kvm")
@patch("chutes_cvm.host.setup._grub_update_cmdline")
@patch("chutes_cvm.host.setup._grub_set_kernel")
@patch("chutes_cvm.host.setup._get_kernel_version", return_value="7.0.0-31-generic")
@patch("chutes_cvm.host.setup._add_repo")
@patch("chutes_cvm.host.setup.run")
@patch("os.geteuid", return_value=0)
def test_setup_host_installs_and_configures_only_its_platform(
    mock_euid,
    mock_run,
    mock_add_repo,
    mock_kver,
    mock_grub_kernel,
    mock_grub_cmdline,
    mock_kvm,
    mock_ntp,
    mock_dirs,
    mock_write,
    mock_tdx_attestation,
    recipe,
):
    is_tdx = recipe.tee is TdxTeeProvider
    setup_host(recipe, noninteractive=True)

    installed = {pkg for c in mock_run.call_args_list for pkg in c[0][0]}
    assert set(recipe.packages) <= installed
    assert ("tdx-qgs" in installed) is is_tdx
    assert [c.args[0].name for c in mock_add_repo.call_args_list] == [
        r.name for r in recipe.repos
    ]
    mock_grub_cmdline.assert_called_once_with(recipe.grub_cmdline_additions)
    assert mock_tdx_attestation.called is is_tdx


@pytest.mark.parametrize(
    "vendor, recipe_cls",
    [("GenuineIntel", TdxUbuntu2604Recipe), ("AuthenticAMD", SnpUbuntu2604Recipe)],
)
@patch("chutes_cvm.host.recipes.detect_ubuntu_version", return_value="26.04")
def test_setup_main_sets_up_the_platform_the_cpu_runs(_version, vendor, recipe_cls):
    from chutes_cvm.host import setup as setup_mod

    with patch.object(
        setup_mod, "detect_cpu_vendor", return_value=vendor
    ), patch.object(setup_mod, "setup_host") as run:
        assert setup_mod.main(["--noninteractive"]) == 0
    assert isinstance(run.call_args.args[0], recipe_cls)
    assert run.call_args.kwargs == {"noninteractive": True}


def test_setup_main_refuses_an_unknown_cpu_before_touching_the_host(capsys):
    from chutes_cvm.host import setup as setup_mod

    with patch.object(
        setup_mod, "detect_cpu_vendor", return_value="HygonGenuine"
    ), patch.object(setup_mod, "setup_host") as run:
        assert setup_mod.main([]) == 1
    run.assert_not_called()
    assert "HygonGenuine" in capsys.readouterr().err
