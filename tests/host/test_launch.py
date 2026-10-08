"""Tests for the Python launch orchestrator (chutes_cvm.guest.launch).

Covers the decision layer ported from quick-launch.sh — CLI override plumbing, derived defaults,
validation, the duplicate-VM guard, the TDX gate, and launch-vm argument assembly. Config
precedence itself (CLI > env > YAML > defaults) lives in the LaunchConfig model and is tested in
test_config.py. All privileged steps (volumes/network/boot) and host probes are mocked here.
"""

from unittest.mock import MagicMock, patch

import pytest
import topology_fixtures as known
from chutes_cvm.guest import image_set, images, launch
from chutes_cvm.guest.chutes_api import ChutesApiError
from chutes_cvm.guest.config import LaunchConfig, cli_fields
from chutes_cvm.guest.host_class import HostClassStatus, MeasuredImage, TdxHostClass
from chutes_cvm.guest.launch import (
    LaunchError,
    _apply_derived_defaults,
    _boot,
    _build_parser,
    _resolve_config,
    _validate,
)

P = "chutes_cvm.guest.launch"
IMAGES = "chutes_cvm.guest.images"


# Convenience: build a LaunchConfig from flat kwargs (mapped to the model's nested fields).
_FLAT_TO_PATH = {
    "hostname": "vm.hostname",
    "base_image": "vm.base_image",
    "vm_image_dir": "vm.vm_image_directory",
    "miner_ss58": "miner.ss58",
    "miner_seed": "miner.seed",
    "vm_ip": "network.vm_ip",
    "network_type": "network.type",
    "cache_volume": "volumes.cache.path",
    "storage_volume": "volumes.storage.path",
    "config_volume": "volumes.config.path",
    "foreground": "runtime.foreground",
}


def _cfg(**over) -> LaunchConfig:
    """A LaunchConfig with model defaults, overlaid with `over` given as flat kwargs."""
    config = LaunchConfig()
    for key, val in over.items():
        obj_path, _, field = _FLAT_TO_PATH[key].rpartition(".")
        obj = config
        for part in obj_path.split("."):
            obj = getattr(obj, part)
        setattr(obj, field, val)
    return config


# ── CLI override plumbing into the model ─────────────────────────────────────────


def test_resolve_config_applies_cli_overrides():
    args = _build_parser().parse_args(["--hostname", "h", "--skip-bind", "--no-gpus"])
    cfg, benchmark, pass_gpus, ephemeral = _resolve_config(args)
    assert cfg.vm.hostname == "h"
    assert cfg.devices.bind_devices is False  # --skip-bind → bind_devices False
    assert pass_gpus is False
    assert benchmark is False and ephemeral is False


def test_no_gpus_and_foreground_flags():
    args = _build_parser().parse_args(["--no-gpus", "--foreground", "--benchmark"])
    cfg, benchmark, pass_gpus, ephemeral = _resolve_config(args)
    assert pass_gpus is False
    assert cfg.runtime.foreground is True
    assert benchmark is True


def test_docker_creds_must_be_paired():
    args = _build_parser().parse_args(["--docker-hub-username", "u"])
    with pytest.raises(LaunchError, match="together"):
        _resolve_config(args)


# ── derived defaults ─────────────────────────────────────────────────────────────


def test_derived_volume_names_from_hostname():
    cfg = _cfg(hostname="box1")
    _apply_derived_defaults(cfg, benchmark=False, ephemeral=False)
    assert cfg.volumes.cache.path == "cache-box1.raw"
    assert cfg.volumes.storage.path == "storage-box1.raw"
    assert cfg.volumes.config.path == "config-box1.qcow2"
    assert cfg.vm.base_image.endswith("tdx-guest")
    assert cfg.vm.vm_image_directory == "/var/lib/chutes/vm-images"


def test_ephemeral_uses_tmp_image_dir():
    cfg = _cfg(hostname="b")
    _apply_derived_defaults(cfg, benchmark=False, ephemeral=True)
    assert cfg.vm.vm_image_directory == "/tmp/chutes-vm-images"


def test_benchmark_fills_placeholders_and_image():
    cfg = _cfg(hostname="b")
    _apply_derived_defaults(cfg, benchmark=True, ephemeral=False)
    assert cfg.vm.base_image.endswith("tdx-guest-benchmark")
    assert cfg.miner.ss58 == "benchmark"
    assert cfg.miner.seed == "benchmark"


# ── validation ───────────────────────────────────────────────────────────────────


def test_validate_requires_creds_in_standard_mode():
    with pytest.raises(LaunchError, match="miner.ss58"):
        _validate(_cfg(hostname="h"), benchmark=False)


def test_validate_requires_hostname():
    with pytest.raises(LaunchError, match="hostname"):
        _validate(_cfg(), benchmark=True)


def test_validate_benchmark_only_needs_hostname():
    _validate(_cfg(hostname="h"), benchmark=True)  # no creds required — must not raise


def test_validate_rejects_bad_network_type():
    cfg = _cfg(hostname="h", miner_ss58="x", miner_seed="y", network_type="bad")
    with pytest.raises(LaunchError, match="network type"):
        _validate(cfg, benchmark=False)


# ── launch-vm argument assembly ──────────────────────────────────────────────────


def _boot_context(cfg, vm_image, net_iface, *, benchmark, pass_gpus, host):
    """Run a test boot through _boot with the factory and the boot primitive faked; return the
    factory's keyword arguments, after checking the primitive got exactly what it built.
    """
    with patch(f"{P}.LaunchContext.from_host", return_value="ctx") as from_host, patch(
        f"{P}.launch_vm", return_value=0
    ) as lv:
        rc = _boot(
            cfg,
            vm_image,
            net_iface,
            benchmark=benchmark,
            pass_gpus=pass_gpus,
            host=host,
            measured=None,
        )
    assert rc == 0
    # The primitive receives the context and the caller's profile -- not an argv list it has to
    # re-parse, and not a profile it reads for itself.
    assert lv.call_args.args == ("ctx", host)
    assert from_host.call_args.args == (host, None)
    return from_host.call_args.kwargs


def test_boot_hands_step_1_s_entry_to_the_context():
    entry = MeasuredImage("1.4.0", False)
    host = _fake_host()
    cfg = _cfg(
        config_volume="c", cache_volume="ca", storage_volume="s", network_type="user"
    )
    with patch(f"{P}.LaunchContext.from_host", return_value="ctx") as from_host, patch(
        f"{P}.launch_vm", return_value=0
    ) as lv:
        assert _boot(cfg, "/img", "", False, True, host, entry) == 0
    assert from_host.call_args.args == (host, entry)
    assert lv.call_args.args == ("ctx", host)


def test_boot_standard_args():
    cfg = _cfg(
        config_volume="c.qcow2",
        cache_volume="ca.raw",
        storage_volume="s.raw",
        network_type="tap",
        foreground=True,
    )
    got = _boot_context(
        cfg, "/img.qcow2", "tap0", benchmark=False, pass_gpus=True, host=_fake_host()
    )
    assert got["image"] == "/img.qcow2"
    assert got["pass_gpus"] is True
    assert got["network"].net_iface == "tap0"
    assert got["volumes"].cache == "ca.raw" and got["process"].foreground is True
    assert got["show_ssh"] is False


def test_boot_plumbs_the_configured_ssh_port():
    """`network.ssh_port` is a config field and a CLI flag, and it never reached the primitive:
    _boot assembled an argv list with no --ssh-port, so the boot parser's own default (10022)
    won and an operator's setting was silently ignored. A context carries it by construction.
    """
    cfg = _cfg(
        config_volume="c", cache_volume="ca", storage_volume="s", network_type="user"
    )
    cfg.network.ssh_port = 2222
    got = _boot_context(
        cfg, "/img", "", benchmark=False, pass_gpus=False, host=_fake_host()
    )
    assert got["network"].ssh_port == 2222


def test_boot_benchmark_omits_cache_adds_ssh():
    cfg = _cfg(config_volume="c", storage_volume="s", network_type="tap")
    got = _boot_context(
        cfg, "/img", "tap0", benchmark=True, pass_gpus=False, host=_fake_host()
    )
    assert got["show_ssh"] is True
    assert got["volumes"].cache is None
    assert got["pass_gpus"] is False


def test_boot_user_network_omits_net_iface():
    cfg = _cfg(
        config_volume="c", cache_volume="ca", storage_volume="s", network_type="user"
    )
    got = _boot_context(
        cfg, "/img", "", benchmark=False, pass_gpus=True, host=_fake_host()
    )
    assert got["network"].net_iface is None


# ── main() orchestration (all steps + probes mocked) ─────────────────────────────

_STD_ARGV = [
    "--hostname",
    "h",
    "--miner-ss58",
    "x",
    "--miner-seed",
    "y",
    "--network-type",
    "user",
    "--no-gpus",
]


def _fake_host(label="Intel TDX", raises=None):
    """A stand-in HostProfile: verify_environment() is the whole surface Step 0 touches."""
    host = MagicMock()
    host.tee_provider.label = label
    host.verify_environment.side_effect = raises
    return host


def _happy(**over):
    """ExitStack of patches for a passing host; `over` overrides individual return values."""
    from contextlib import ExitStack

    stack = ExitStack()
    # Step 0 now takes THE reading of this host and asks the profile whether it can launch,
    # so the seam under test is from_host() rather than a launcher-local probe.
    host = over.pop("host", None) or _fake_host()
    stack.enter_context(
        patch("chutes_cvm.guest.host_profile.HostProfile.from_host", return_value=host)
    )
    defaults = {
        "resolve_public_iface": "eth0",
        "_chutes_td_running": False,
        "_measured_image": MeasuredImage("1.4.0", False),
        "prepare_vm_image": "/var/lib/chutes/vm-images/img.qcow2",
    }
    defaults.update(over)
    for name, ret in defaults.items():
        if isinstance(ret, Exception):
            stack.enter_context(patch(f"{P}.{name}", side_effect=ret))
        else:
            stack.enter_context(patch(f"{P}.{name}", return_value=ret))
    for name in (
        "_ensure_numa_zone_reclaim",
        "ensure_raw_volume",
        "setup_config_volume",
    ):
        stack.enter_context(patch(f"{P}.{name}"))
    return stack


def test_removed_and_abbreviated_flags_are_rejected():
    """`--config` shared the positional's dest and never worked -- an optional positional applies
    its default even when it matches zero args, so it clobbered the flag and the launch silently
    used the default config path. Removing it was not enough on its own: argparse prefix matching
    then resolved `--config` to `--config-volume`, reading a launch config path as a config VOLUME
    path. Abbreviations are off, so both are now errors instead of quiet misreads."""
    parser = launch._build_parser()
    for argv in (["--config", "x.yaml"], ["--vm-d", "1.1.1.1"], ["--bench"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)

    assert parser.parse_args(["x.yaml"]).config_file == "x.yaml"


def test_main_happy_path_user_network():
    with _happy(), patch(f"{P}._boot", return_value=0) as boot:
        rc = launch.main(_STD_ARGV)
    assert rc == 0
    boot.assert_called_once()


def test_main_refuses_duplicate_without_force(capsys):
    with _happy(_chutes_td_running=True):
        rc = launch.main(_STD_ARGV)
    assert rc == 1
    assert "already running" in capsys.readouterr().err


def test_main_force_overrides_duplicate_guard():
    with _happy(_chutes_td_running=True), patch(f"{P}._boot", return_value=0) as boot:
        rc = launch.main(_STD_ARGV + ["--force"])
    assert rc == 0
    boot.assert_called_once()


_REFUSED = LaunchError("this host cannot attest 1.4.0 yet")


def test_main_refuses_when_step_1_refuses():
    # No published measurement for this production image x host class → stop before any
    # GPU/volume work.
    with _happy(_measured_image=_REFUSED), patch(f"{P}._boot") as boot:
        rc = launch.main(_STD_ARGV)
    assert rc == 1
    boot.assert_not_called()


def test_main_benchmark_test_boots_without_asking():
    # Benchmark VMs use dummy creds and aren't attested, so a failing gate must not block them.
    argv = ["--hostname", "h", "--benchmark", "--network-type", "user", "--no-gpus"]
    with _happy(_measured_image=_REFUSED) as _, patch(
        f"{P}._boot", return_value=0
    ) as boot:
        rc = launch.main(argv)
        launch._measured_image.assert_not_called()
    assert rc == 0
    assert boot.call_args.args[-1] is None  # a test boot


# ── Step 1: measured, test boot, or refused ──────────────────────────────────────


def _host_class(*measured):
    return TdxHostClass(
        fingerprint="abc",
        status=HostClassStatus.ACCEPTED,
        measured=list(measured),
        detail="no measurement for this image",
    )


def _step_1(image, host_class=None, *, force=False, fetch_error=None):
    """Run Step 1 against ``image`` (an ImageSet, or an exception reading it) and a class."""
    host = _fake_host()
    read = (
        {"side_effect": image}
        if isinstance(image, Exception)
        else {"return_value": image}
    )
    fetch = (
        {"side_effect": fetch_error} if fetch_error else {"return_value": host_class}
    )
    with patch(f"{P}.ImageSet.from_dir", **read), patch(
        f"{P}.HostClass.fetch", **fetch
    ) as fetched:
        measured = launch._measured_image("/cfg.yaml", "/base", force, host)
    if not isinstance(image, Exception):
        assert fetched.call_args.args == (host,)
    return measured


def test_a_measured_image_launches_measured(capsys):
    image = known.fake_image_set("1.4.0")
    entry = MeasuredImage("1.4.0", False)
    assert _step_1(image, _host_class(entry)) is entry
    assert "is measured" in capsys.readouterr().out


def test_an_unmeasured_production_image_is_refused_unless_forced(capsys):
    image = known.fake_image_set("1.4.0")
    host_class = _host_class(MeasuredImage("1.4.0", True))
    with pytest.raises(LaunchError, match="Refusing to launch") as refused:
        _step_1(image, host_class)
    assert "submit-profile" in str(refused.value)
    assert _step_1(image, host_class, force=True) is None
    assert "Test boot (--force)" in capsys.readouterr().err


def test_an_unmeasured_debug_image_test_boots(capsys):
    """A debug build that is not published boots so it can be tested; it cannot attest."""
    image = known.fake_image_set("1.4.0", rc=True)
    assert _step_1(image, _host_class(MeasuredImage("1.4.0", False))) is None
    assert "Test boot (debug build)" in capsys.readouterr().err


def test_a_published_debug_image_launches_measured():
    """rc:true is joined like any other version: published, it is a measured launch."""
    image = known.fake_image_set("1.4.0", rc=True)
    entry = MeasuredImage("1.4.0", True)
    assert _step_1(image, _host_class(entry)) is entry


def test_no_answer_from_the_api_counts_as_unmeasured():
    error = ChutesApiError("API unreachable")
    production, debug = known.fake_image_set("1.4.0"), known.fake_image_set(
        "1.4.0", rc=True
    )
    with pytest.raises(LaunchError, match="API unreachable"):
        _step_1(production, fetch_error=error)
    assert _step_1(production, fetch_error=error, force=True) is None
    assert _step_1(debug, fetch_error=error) is None


def test_an_unreadable_manifest_is_refused_unless_forced():
    # Can't read the image version → can't know what we're booting → fail closed.
    with pytest.raises(LaunchError, match="image version"):
        _step_1(FileNotFoundError("no manifest"))
    assert _step_1(FileNotFoundError("no manifest"), force=True) is None


def test_main_blocks_when_the_platform_is_not_enabled(capsys):
    """A host whose platform is off in BIOS cannot launch a confidential guest. The message
    comes from the provider, so it names THIS host's platform and its remedy."""
    host = _fake_host(
        raises=RuntimeError(
            "This host profile is AMD SEV-SNP, but the machine reports ..."
        )
    )
    with _happy(host=host):
        rc = launch.main(_STD_ARGV)
    assert rc == 1
    assert "AMD SEV-SNP" in capsys.readouterr().err


def test_main_launches_on_an_amd_host():
    """The gate is the profile's own platform, not whether it is Intel."""
    with _happy(host=_fake_host(label="AMD SEV-SNP")), patch(
        f"{P}._boot", return_value=0
    ):
        rc = launch.main(_STD_ARGV)
    assert rc == 0


def test_main_signs_and_boots_one_single_reading_of_the_host():
    """discover-profile.sh runs once per launch. The profile preflight signs -- and gets a
    launchable verdict for -- must be the same object the boot primitive builds the command
    from, or the control plane can approve a shape that never boots."""
    host = _fake_host()
    entry = MeasuredImage("1.4.0", False)
    with _happy(host=host), patch(f"{P}._boot", return_value=0) as boot, patch(
        f"{P}._measured_image", return_value=entry
    ) as step_1:
        assert launch.main(_STD_ARGV) == 0

    assert boot.call_args.args[-2:] == (host, entry)
    assert step_1.call_args.args[-1] is host


def test_main_missing_creds_is_error(capsys):
    with _happy():
        rc = launch.main(["--hostname", "h", "--network-type", "user"])
    assert rc == 1
    assert "miner.ss58" in capsys.readouterr().err


def _stage_image_set(tmp_path):
    """A real base image-set dir: a qcow2, its 3 direct-boot sidecars, and the manifest the
    build would write. Returns (set_dir, qcow2, the qcow2's manifest sha256)."""
    base = tmp_path / "base"
    base.mkdir()
    qcow2 = base / "x.qcow2"
    qcow2.write_bytes(b"q")
    for ext in ("vmlinuz", "initrd", "cmdline"):
        (base / f"x.{ext}").write_bytes(b"s")
    image_set.write_manifest(str(qcow2), str(base / "manifest.json"), version="1.4.0")
    return str(base), str(qcow2), image_set.ImageSet.from_dir(str(base)).qcow2.sha256


def test_prepare_vm_image_verifies_in_python_then_copies_via_sudo(tmp_path):
    """The image set is read and verified in Python (``ImageSet``); the privileged file
    mutations are done in-process as `sudo cp` (root-owned image dir), not shelled to a script
    that guessed a Python interpreter."""
    set_dir, qcow2, sha = _stage_image_set(tmp_path)
    vm_dir = tmp_path / "vm-images"
    vm_dir.mkdir()
    calls: list[list[str]] = []

    with patch(f"{IMAGES}.run", side_effect=lambda cmd, **k: calls.append(cmd)):
        out = images.prepare_vm_image(set_dir, "h", str(vm_dir))

    vm_image = str(vm_dir / f"tdx-h-{sha[:16]}.qcow2")
    assert out == vm_image
    # qcow2 + 3 sidecars, each copied via `sudo cp`, into the per-VM name.
    cps = [c for c in calls if c[:2] == ["sudo", "cp"]]
    assert cps[0] == ["sudo", "cp", qcow2, vm_image]
    assert [c[-1] for c in cps[1:]] == [
        str(vm_dir / f"tdx-h-{sha[:16]}.{ext}")
        for ext in ("vmlinuz", "initrd", "cmdline")
    ]


def test_prepare_vm_image_reaps_stale_versions(tmp_path):
    set_dir, _, _ = _stage_image_set(tmp_path)
    vm_dir = tmp_path / "vm"
    vm_dir.mkdir()
    stale = vm_dir / "tdx-h-oldoldoldoldold0.qcow2"  # a previous version's per-VM copy
    stale.write_bytes(b"old")
    calls: list[list[str]] = []

    with patch(f"{IMAGES}.run", side_effect=lambda cmd, **k: calls.append(cmd)):
        images.prepare_vm_image(set_dir, "h", str(vm_dir))

    rms = [c for c in calls if c[:2] == ["sudo", "rm"]]
    assert len(rms) == 1
    # the stale qcow2 AND its sidecars are removed
    assert str(stale) in rms[0]
    assert str(vm_dir / "tdx-h-oldoldoldoldold0.vmlinuz") in rms[0]


def test_prepare_vm_image_refuses_a_set_missing_a_sidecar(tmp_path):
    set_dir, _, _ = _stage_image_set(tmp_path)
    (tmp_path / "base" / "x.initrd").unlink()
    with patch(f"{IMAGES}.run"):
        with pytest.raises(LaunchError, match="missing initrd"):
            images.prepare_vm_image(set_dir, "h", str(tmp_path / "vm"))


def test_prepare_vm_image_surfaces_verification_failure(tmp_path):
    set_dir, qcow2, _ = _stage_image_set(tmp_path)
    with open(qcow2, "ab") as f:
        f.write(b"grown")
    with pytest.raises(LaunchError, match="image set verification failed"):
        images.prepare_vm_image(set_dir, "h", str(tmp_path / "vm"))


def test_prepare_vm_image_refuses_firmware_the_image_was_not_built_with(
    tmp_path, monkeypatch
):
    """chutes-cvm's firmware ships apart from the image, and the launch measurement covers its
    bytes: a mismatch would boot a guest that cannot attest."""
    set_dir, _, _ = _stage_image_set(tmp_path)
    retired = tmp_path / "retired"
    retired.mkdir()
    for name in image_set.FIRMWARE:
        (retired / name).write_bytes(b"retired firmware")
    monkeypatch.setenv("CHUTES_CVM_FIRMWARE_DIR", str(retired))
    with patch(f"{IMAGES}.run") as run:
        with pytest.raises(LaunchError, match="was built with"):
            images.prepare_vm_image(set_dir, "h", str(tmp_path / "vm"))
    run.assert_not_called()


def test_every_launch_flag_has_help():
    """Config flags take their help from the model's field descriptions; a flag without one is
    a field missing its description."""
    parser = launch._build_parser()
    missing = [
        a.option_strings[0]
        for a in parser._actions
        if a.option_strings and a.dest != "help" and not a.help
    ]
    assert missing == []


def test_shared_volume_flags_say_which_volume():
    helps = {flag: help_ for flag, _path, _ann, help_ in cli_fields()}
    assert helps["--cache-size"].startswith("Cache volume")
    assert helps["--storage-size"].startswith("Storage volume")
