"""Tests for the cache-rm privileged-remove wrapper.

cache-rm is granted to system-manager via sudoers INSTEAD of bare `rm`, so it is
the security boundary: it must only ever recursively remove a DIRECT child of the
HF cache base, and must refuse the base itself, deeper subtrees, and any target
that resolves (via symlink or `..`) outside the cache.

The wrapper hardcodes CACHE_BASE=/var/snap/cache, which can't be created in CI, so
each test runs a copy with the base rewritten to a tmp dir. `rm` is stubbed earlier
on PATH so the destructive `exec rm` is observed, never executed. One test pins the
shipped script's hardcoded base so the rewrite can't mask a regression.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ansible/guest/roles/system-manager/files/cache-rm"


def _harness(tmp_path):
    """Return (run, base, rm_log). `run(*args)` invokes the wrapper (base rewritten
    to a tmp dir) with a recording `rm` stub on PATH."""
    src = SCRIPT.read_text()
    assert (
        'CACHE_BASE="/var/snap/cache"' in src
    ), "shipped wrapper must hardcode the cache base"

    base = tmp_path / "cache"
    base.mkdir()
    patched = src.replace("/var/snap/cache", str(base))
    script = tmp_path / "cache-rm"
    script.write_text(patched)
    script.chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rm_log = tmp_path / "rm.log"
    stub = bin_dir / "rm"
    stub.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" >> "{rm_log}"\n')
    stub.chmod(0o755)

    def run(*args):
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            ["bash", str(script), *args],
            env=env,
            capture_output=True,
            text=True,
        )

    return run, base, rm_log


def _rm_calls(rm_log):
    return rm_log.read_text() if rm_log.exists() else ""


def test_removes_direct_child(tmp_path):
    run, base, rm_log = _harness(tmp_path)
    chute = base / "2ff25e81-4586-5ec8-b892-3a6f342693d7"
    chute.mkdir()
    result = run(str(chute))
    assert result.returncode == 0, result.stderr
    assert str(chute) in _rm_calls(rm_log)


def test_refuses_cache_base_itself(tmp_path):
    run, base, rm_log = _harness(tmp_path)
    result = run(str(base))
    assert result.returncode != 0
    assert _rm_calls(rm_log) == ""


def test_refuses_nested_subtree(tmp_path):
    run, base, rm_log = _harness(tmp_path)
    nested = base / "chute" / "hub" / "blobs"
    nested.mkdir(parents=True)
    result = run(str(nested))
    assert result.returncode != 0
    assert _rm_calls(rm_log) == ""


def test_refuses_path_outside_cache(tmp_path):
    run, _base, rm_log = _harness(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    result = run(str(outside))
    assert result.returncode != 0
    assert _rm_calls(rm_log) == ""


def test_refuses_symlink_escape(tmp_path):
    run, base, rm_log = _harness(tmp_path)
    outside = tmp_path / "secret"
    outside.mkdir()
    link = base / "evil"  # direct child by name, but resolves outside
    link.symlink_to(outside, target_is_directory=True)
    result = run(str(link))
    assert result.returncode != 0
    assert _rm_calls(rm_log) == ""
    assert outside.exists()


def test_refuses_dotdot_escape(tmp_path):
    run, base, rm_log = _harness(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    result = run(f"{base}/../outside")
    assert result.returncode != 0
    assert _rm_calls(rm_log) == ""


def test_missing_target_is_noop(tmp_path):
    run, base, rm_log = _harness(tmp_path)
    result = run(str(base / "does-not-exist"))
    assert result.returncode == 0
    assert _rm_calls(rm_log) == ""


@pytest.mark.parametrize("args", [[], ["a", "b"]])
def test_rejects_wrong_arg_count(tmp_path, args):
    run, _base, rm_log = _harness(tmp_path)
    result = run(*args)
    assert result.returncode == 2
    assert _rm_calls(rm_log) == ""
