"""Unit tests for post-launch host tuning helpers."""

import os
from unittest.mock import MagicMock, patch

from chutes.guest.post_launch import (
    apply_post_launch_tuning,
    expand_cpulist,
    find_qemu_pid,
    restore_host_tuning,
    tune_host_cpu_power,
)

# ---------------------------------------------------------------------------
# expand_cpulist
# ---------------------------------------------------------------------------


def test_expand_cpulist_single_range():
    assert expand_cpulist("0-3") == [1, 2, 3]


def test_expand_cpulist_multiple_ranges():
    assert expand_cpulist("0-1,4-5") == [1, 4, 5]


def test_expand_cpulist_excludes_cpu_zero():
    assert 0 not in expand_cpulist("0,2,4")


def test_expand_cpulist_single_cpus():
    assert expand_cpulist("1,3,5") == [1, 3, 5]


def test_expand_cpulist_empty_string():
    assert expand_cpulist("") == []


# ---------------------------------------------------------------------------
# Helpers shared across tune_host_cpu_power tests
# ---------------------------------------------------------------------------

_GOV_FILES = [
    "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
    "/sys/devices/system/cpu/cpu1/cpufreq/scaling_governor",
]
_IDLE_FILES = [
    "/sys/devices/system/cpu/cpu0/cpuidle/state1/disable",
    "/sys/devices/system/cpu/cpu0/cpuidle/state2/disable",
]
_NO_TURBO = "/sys/devices/system/cpu/intel_pstate/no_turbo"


def _glob_side_effect(pattern):
    if "scaling_governor" in pattern:
        return _GOV_FILES
    if "cpuidle" in pattern:
        return _IDLE_FILES
    return []


def _open_side_effect(original_saved):
    """Return a side_effect for open() that reads 'original_saved' from sysfs files."""

    def _open(path, *args, **kwargs):
        if any(path == f for f in _GOV_FILES + _IDLE_FILES + [_NO_TURBO]):
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            m.read.return_value = original_saved
            return m
        return original_open(path, *args, **kwargs)

    original_open = open
    return _open


def _isfile_side_effect(restore_path: str, restore_exists: bool):
    """Return True for no_turbo always; return restore_exists for the restore script."""

    def _isfile(path):
        if path == restore_path:
            return restore_exists
        # Default: return True for no_turbo and other sysfs paths
        return True

    return _isfile


# ---------------------------------------------------------------------------
# tune_host_cpu_power — first call: snapshots original state
# ---------------------------------------------------------------------------


def test_tune_first_call_writes_restore_script(tmp_path):
    restore_path = str(tmp_path / "restore.sh")

    with (
        patch("chutes.guest.post_launch.RESTORE_SCRIPT", restore_path),
        patch("chutes.guest.post_launch.glob.glob", side_effect=_glob_side_effect),
        patch(
            "chutes.guest.post_launch.os.path.isfile",
            side_effect=_isfile_side_effect(restore_path, False),
        ),
        patch("builtins.open", side_effect=_open_side_effect("powersave")),
        patch("chutes.guest.post_launch._write_root"),
        patch("chutes.guest.post_launch.os.makedirs"),
        patch("chutes.guest.post_launch.os.chmod"),
    ):
        tune_host_cpu_power()

    assert os.path.isfile(restore_path)
    content = open(restore_path).read()
    assert "powersave" in content
    assert "scaling_governor" in content
    assert "no_turbo" in content
    assert "cpuidle" in content
    assert "Host CPU settings restored." in content


def test_tune_first_call_applies_settings(tmp_path):
    restore_path = str(tmp_path / "restore.sh")
    write_root = MagicMock()

    with (
        patch("chutes.guest.post_launch.RESTORE_SCRIPT", restore_path),
        patch("chutes.guest.post_launch.glob.glob", side_effect=_glob_side_effect),
        patch(
            "chutes.guest.post_launch.os.path.isfile",
            side_effect=_isfile_side_effect(restore_path, False),
        ),
        patch("builtins.open", side_effect=_open_side_effect("powersave")),
        patch("chutes.guest.post_launch._write_root", write_root),
        patch("chutes.guest.post_launch.os.makedirs"),
        patch("chutes.guest.post_launch.os.chmod"),
    ):
        tune_host_cpu_power()

    gov_calls = [
        c for c in write_root.call_args_list if "scaling_governor" in c.args[0]
    ]
    assert all(c.args[1] == "performance" for c in gov_calls)

    idle_calls = [c for c in write_root.call_args_list if "cpuidle" in c.args[0]]
    assert all(c.args[1] == "1" for c in idle_calls)

    turbo_calls = [c for c in write_root.call_args_list if "no_turbo" in c.args[0]]
    assert turbo_calls and turbo_calls[0].args[1] == "0"


# ---------------------------------------------------------------------------
# tune_host_cpu_power — idempotency: existing restore script is NOT overwritten
# ---------------------------------------------------------------------------


def test_tune_second_call_does_not_overwrite_restore_script(tmp_path):
    restore_path = str(tmp_path / "restore.sh")
    original_content = "# original snapshot\necho powersave | sudo tee ...\n"
    with open(restore_path, "w") as f:
        f.write(original_content)

    with (
        patch("chutes.guest.post_launch.RESTORE_SCRIPT", restore_path),
        patch("chutes.guest.post_launch.glob.glob", side_effect=_glob_side_effect),
        patch(
            "chutes.guest.post_launch.os.path.isfile",
            side_effect=_isfile_side_effect(restore_path, True),
        ),
        patch("builtins.open", side_effect=_open_side_effect("performance")),
        patch("chutes.guest.post_launch._write_root"),
        patch("chutes.guest.post_launch.os.makedirs"),
        patch("chutes.guest.post_launch.os.chmod"),
    ):
        tune_host_cpu_power()

    assert open(restore_path).read() == original_content


def test_tune_second_call_still_reapplies_settings(tmp_path):
    restore_path = str(tmp_path / "restore.sh")
    with open(restore_path, "w") as f:
        f.write("# existing\n")

    write_root = MagicMock()

    with (
        patch("chutes.guest.post_launch.RESTORE_SCRIPT", restore_path),
        patch("chutes.guest.post_launch.glob.glob", side_effect=_glob_side_effect),
        patch(
            "chutes.guest.post_launch.os.path.isfile",
            side_effect=_isfile_side_effect(restore_path, True),
        ),
        patch("builtins.open", side_effect=_open_side_effect("performance")),
        patch("chutes.guest.post_launch._write_root", write_root),
        patch("chutes.guest.post_launch.os.makedirs"),
        patch("chutes.guest.post_launch.os.chmod"),
    ):
        tune_host_cpu_power()

    assert write_root.call_count > 0
    idle_calls = [c for c in write_root.call_args_list if "cpuidle" in c.args[0]]
    assert all(c.args[1] == "1" for c in idle_calls)


# ---------------------------------------------------------------------------
# restore_host_tuning
# ---------------------------------------------------------------------------


def test_restore_runs_script_when_present(tmp_path):
    restore_path = str(tmp_path / "restore.sh")
    with open(restore_path, "w") as f:
        f.write("#!/usr/bin/env bash\necho restored\n")
    os.chmod(restore_path, 0o755)

    run = MagicMock(return_value=MagicMock(returncode=0))
    with (
        patch("chutes.guest.post_launch.RESTORE_SCRIPT", restore_path),
        patch("chutes.guest.post_launch.subprocess.run", run),
    ):
        restore_host_tuning()

    run.assert_called_once_with([restore_path], check=False)


def test_restore_is_noop_when_script_missing(tmp_path):
    restore_path = str(tmp_path / "nonexistent.sh")
    run = MagicMock()

    with (
        patch("chutes.guest.post_launch.RESTORE_SCRIPT", restore_path),
        patch("chutes.guest.post_launch.subprocess.run", run),
    ):
        restore_host_tuning()

    run.assert_not_called()


def test_restore_warns_on_nonzero_exit(tmp_path, capsys):
    restore_path = str(tmp_path / "restore.sh")
    with open(restore_path, "w") as f:
        f.write("#!/usr/bin/env bash\nexit 1\n")

    run = MagicMock(return_value=MagicMock(returncode=1))
    with (
        patch("chutes.guest.post_launch.RESTORE_SCRIPT", restore_path),
        patch("chutes.guest.post_launch.subprocess.run", run),
    ):
        restore_host_tuning()

    captured = capsys.readouterr()
    assert "Warning" in captured.out


# ---------------------------------------------------------------------------
# find_qemu_pid
# ---------------------------------------------------------------------------


def test_find_qemu_pid_returns_pid_when_pidfile_and_proc_exist(tmp_path):
    pidfile = tmp_path / "td.pid"
    pidfile.write_text("12345\n")

    with patch("chutes.guest.post_launch.os.path.exists", return_value=True):
        pid = find_qemu_pid(pidfile=str(pidfile))

    assert pid == 12345


def test_find_qemu_pid_returns_none_when_pidfile_missing(tmp_path):
    assert find_qemu_pid(pidfile=str(tmp_path / "missing.pid")) is None


def test_find_qemu_pid_returns_none_when_process_gone(tmp_path):
    pidfile = tmp_path / "td.pid"
    pidfile.write_text("99999\n")

    with patch("chutes.guest.post_launch.os.path.exists", return_value=False):
        pid = find_qemu_pid(pidfile=str(pidfile))

    assert pid is None


def test_find_qemu_pid_returns_none_for_corrupt_pidfile(tmp_path):
    pidfile = tmp_path / "td.pid"
    pidfile.write_text("not-a-number\n")
    assert find_qemu_pid(pidfile=str(pidfile)) is None


# ---------------------------------------------------------------------------
# apply_post_launch_tuning — tune_cpu gate
# ---------------------------------------------------------------------------


def test_apply_calls_tune_when_tune_cpu_true(tmp_path):
    tune = MagicMock()
    pin = MagicMock()

    with (
        patch("chutes.guest.post_launch.tune_host_cpu_power", tune),
        patch("chutes.guest.post_launch.pin_qemu_threads", pin),
        patch("chutes.guest.post_launch.find_qemu_pid", return_value=None),
        patch("chutes.guest.post_launch.time.sleep"),
    ):
        apply_post_launch_tuning(
            pidfile="/tmp/fake.pid",
            vcpus_total=16,
            host_nodes=[0, 1],
            pin_threads=False,
            tune_cpu=True,
        )

    tune.assert_called_once()
    pin.assert_not_called()


def test_apply_skips_tune_when_tune_cpu_false(tmp_path):
    tune = MagicMock()

    with (
        patch("chutes.guest.post_launch.tune_host_cpu_power", tune),
        patch("chutes.guest.post_launch.time.sleep"),
    ):
        apply_post_launch_tuning(
            pidfile="/tmp/fake.pid",
            vcpus_total=16,
            host_nodes=[0, 1],
            pin_threads=False,
            tune_cpu=False,
        )

    tune.assert_not_called()


# ---------------------------------------------------------------------------
# apply_post_launch_tuning — pin_threads gate
# ---------------------------------------------------------------------------


def test_apply_pins_threads_when_pid_found(tmp_path):
    pin = MagicMock()

    with (
        patch("chutes.guest.post_launch.tune_host_cpu_power"),
        patch("chutes.guest.post_launch.find_qemu_pid", return_value=42),
        patch("chutes.guest.post_launch.pin_qemu_threads", pin),
        patch("chutes.guest.post_launch.time.sleep"),
    ):
        apply_post_launch_tuning(
            pidfile="/tmp/fake.pid",
            vcpus_total=16,
            host_nodes=[0, 1],
            pin_threads=True,
            tune_cpu=False,
        )

    pin.assert_called_once_with(42, vcpus_total=16, host_nodes=[0, 1])


def test_apply_skips_pin_when_pin_threads_false():
    pin = MagicMock()

    with (
        patch("chutes.guest.post_launch.tune_host_cpu_power"),
        patch("chutes.guest.post_launch.pin_qemu_threads", pin),
        patch("chutes.guest.post_launch.time.sleep"),
    ):
        apply_post_launch_tuning(
            pidfile="/tmp/fake.pid",
            vcpus_total=16,
            host_nodes=[0, 1],
            pin_threads=False,
            tune_cpu=False,
        )

    pin.assert_not_called()


def test_apply_warns_when_pid_not_found(capsys):
    pin = MagicMock()

    with (
        patch("chutes.guest.post_launch.find_qemu_pid", return_value=None),
        patch("chutes.guest.post_launch.pin_qemu_threads", pin),
        patch("chutes.guest.post_launch.time.sleep"),
    ):
        apply_post_launch_tuning(
            pidfile="/tmp/fake.pid",
            vcpus_total=16,
            host_nodes=[0, 1],
            pin_threads=True,
            tune_cpu=False,
        )

    pin.assert_not_called()
    assert "Warning" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Full lifecycle: first launch → crash → relaunch → clean stop
# ---------------------------------------------------------------------------


def test_full_lifecycle_preserves_original_state(tmp_path):
    """Simulate: tune → VM crash → retune → restore. Original state must survive."""
    restore_path = str(tmp_path / "restore.sh")

    common_patches = dict(
        glob=patch("chutes.guest.post_launch.glob.glob", side_effect=_glob_side_effect),
        write_root=patch("chutes.guest.post_launch._write_root"),
        makedirs=patch("chutes.guest.post_launch.os.makedirs"),
        chmod=patch("chutes.guest.post_launch.os.chmod"),
        restore_script=patch("chutes.guest.post_launch.RESTORE_SCRIPT", restore_path),
    )

    # --- First launch: host is in powersave, no restore script yet ---
    with (
        common_patches["restore_script"],
        common_patches["glob"],
        patch(
            "chutes.guest.post_launch.os.path.isfile",
            side_effect=_isfile_side_effect(restore_path, False),
        ),
        patch("builtins.open", side_effect=_open_side_effect("powersave")),
        common_patches["write_root"],
        common_patches["makedirs"],
        common_patches["chmod"],
    ):
        tune_host_cpu_power()

    assert os.path.isfile(restore_path)
    first_content = open(restore_path).read()
    assert "powersave" in first_content

    # --- VM crashes; restore script now exists; sysfs reads "performance" ---
    with (
        common_patches["restore_script"],
        common_patches["glob"],
        patch(
            "chutes.guest.post_launch.os.path.isfile",
            side_effect=_isfile_side_effect(restore_path, True),
        ),
        patch("builtins.open", side_effect=_open_side_effect("performance")),
        common_patches["write_root"],
        common_patches["makedirs"],
        common_patches["chmod"],
    ):
        tune_host_cpu_power()

    # Restore script must still contain the *original* powersave snapshot
    assert open(restore_path).read() == first_content

    # --- Clean stop: restore_host_tuning() runs the script ---
    run = MagicMock(return_value=MagicMock(returncode=0))
    with (
        patch("chutes.guest.post_launch.RESTORE_SCRIPT", restore_path),
        patch("chutes.guest.post_launch.subprocess.run", run),
    ):
        restore_host_tuning()

    run.assert_called_once_with([restore_path], check=False)
