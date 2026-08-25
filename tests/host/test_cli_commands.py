"""Tests for the chutes-cvm CLI dispatcher (chutes_cvm.cli).

Covers the command surface after the up->launch rename and the decomposition of
quick-launch's early-exit modes into first-class commands (image download / init / stop / down).
The low-level QEMU primitive is the hidden `launch-vm`; the orchestrator is `launch`.
"""

import os
from unittest.mock import patch

from chutes_cvm import cli


def _visible_commands():
    parser = cli.build_parser()
    # The subparsers action holds the registered command choices.
    subactions = [
        a
        for a in parser._actions
        if getattr(a, "choices", None) and "launch" in a.choices
    ]
    return set(subactions[0].choices)


def test_visible_command_surface():
    cmds = _visible_commands()
    for expected in (
        "launch",
        "image",
        "init",
        "stop",
        "down",
        "verify-host",
        "measurements",
    ):
        assert expected in cmds
    # preflight was folded into `verify-host --submit`; it is no longer its own command.
    assert "preflight" not in cmds
    # launch-vm is the hidden primitive: dispatched via _PASSTHROUGH, never a visible subcommand.
    assert "launch-vm" not in cmds
    assert "up" not in cmds


def test_launch_dispatches_to_orchestrator_script():
    with patch("chutes_cvm.cli._run_script", return_value=0) as run:
        assert cli.main(["launch", "config.yaml", "--foreground"]) == 0
    name, argv = run.call_args.args[0], run.call_args.args[1]
    assert name == "quick-launch.sh"
    assert argv == ["config.yaml", "--foreground"]
    # Orchestrator must run from the bundled scripts dir so ./volumes and ./network resolve.
    assert run.call_args.kwargs["cwd"] == str(cli._SCRIPTS_DIR)


def test_launch_vm_dispatches_to_primitive():
    with patch("chutes_cvm.guest.__main__.main", return_value=7) as prim:
        assert cli.main(["launch-vm", "--image", "x.qcow2"]) == 7
    assert prim.call_args.args[0] == ["--image", "x.qcow2"]


def test_measurements_dispatches_to_engine():
    with patch(
        "chutes_cvm.measurement.generate_measurements.main", return_value=0
    ) as gen:
        assert cli.main(["measurements", "list", "--qemu", "10.2.1"]) == 0
    assert gen.call_args.args[0] == ["list", "--qemu", "10.2.1"]


def test_stop_calls_stop_existing_vm():
    with patch("chutes_cvm.guest.__main__.stop_existing_vm") as stop:
        assert cli.main(["stop"]) == 0
    stop.assert_called_once_with()


def test_down_dispatches_to_teardown_script():
    with patch("chutes_cvm.cli._run_script", return_value=0) as run:
        assert cli.main(["down", "--config", "/nope/config.yaml"]) == 0
    # Non-existent config is not forwarded (teardown falls back to defaults).
    assert run.call_args.args[0] == "teardown.sh"
    assert run.call_args.args[1] == []
    assert run.call_args.kwargs["cwd"] == str(cli._SCRIPTS_DIR)


def test_image_dispatches_to_engine():
    # `chutes-cvm image <verb>` forwards verbatim to the image_set module's main.
    with patch("chutes_cvm.guest.image_set.main", return_value=0) as img:
        assert cli.main(["image", "verify", "/some/dir"]) == 0
    assert img.call_args.args[0] == ["verify", "/some/dir"]


def test_image_download_selects_production_by_default():
    from chutes_cvm.guest import image_set

    with patch("chutes_cvm.guest.image_set.subprocess.call", return_value=0) as call:
        assert image_set.main(["download"]) == 0
    # download-image-set.sh is invoked with the production variant.
    assert call.call_args.args[0][-1] == "tdx-guest"


def test_image_download_debug_flag_selects_debug_set():
    from chutes_cvm.guest import image_set

    with patch("chutes_cvm.guest.image_set.subprocess.call", return_value=0) as call:
        assert image_set.main(["download", "--debug"]) == 0
    assert call.call_args.args[0][-1] == "tdx-guest-debug"


def test_init_writes_config_and_guards_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    dest = tmp_path / "config.yaml"
    assert dest.exists() and dest.read_text().strip()

    # A second init refuses (non-zero) rather than clobbering an edited config.
    assert cli.main(["init"]) == 1

    # --force overwrites.
    dest.write_text("stale")
    assert cli.main(["init", "--force"]) == 0
    assert dest.read_text() != "stale"


def test_init_template_source_is_bundled():
    # The template `init` copies must ship inside the package (resolved package-relative).
    template = cli._SCRIPTS_DIR / "config" / "config.tmpl.yaml"
    assert os.path.exists(template)
