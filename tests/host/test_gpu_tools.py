"""Tests for the self-healing helpers in the GPU admin tools installer."""

import subprocess
import sys
from unittest.mock import MagicMock, patch

from chutes_cvm.guest.gpu.tools import _cli_healthy, _venv_matches_system_python


def _completed(returncode):
    result = MagicMock()
    result.returncode = returncode
    return result


# ---------------------------------------------------------------------------
# _cli_healthy
# ---------------------------------------------------------------------------


@patch("chutes_cvm.guest.gpu.tools.subprocess.run")
def test_cli_healthy_false_when_not_on_path(mock_run):
    mock_run.return_value = _completed(1)  # `which` fails
    assert _cli_healthy() is False
    mock_run.assert_called_once()  # never probes --help when absent


@patch("chutes_cvm.guest.gpu.tools.subprocess.run")
def test_cli_healthy_false_when_cli_errors(mock_run):
    # On PATH, but --help fails — e.g. ModuleNotFoundError after a Python bump.
    mock_run.side_effect = [_completed(0), _completed(1)]
    assert _cli_healthy() is False
    assert mock_run.call_count == 2


@patch("chutes_cvm.guest.gpu.tools.subprocess.run")
def test_cli_healthy_true_when_help_succeeds(mock_run):
    mock_run.side_effect = [_completed(0), _completed(0)]
    assert _cli_healthy() is True


@patch("chutes_cvm.guest.gpu.tools.subprocess.run")
def test_cli_healthy_false_on_probe_timeout(mock_run):
    mock_run.side_effect = [
        _completed(0),
        subprocess.TimeoutExpired("nvidia-gpu-tools", 15),
    ]
    assert _cli_healthy() is False


# ---------------------------------------------------------------------------
# _venv_matches_system_python
# ---------------------------------------------------------------------------


def test_venv_matches_false_when_cfg_missing(tmp_path):
    assert _venv_matches_system_python(str(tmp_path)) is False


def test_venv_matches_true_for_current_python(tmp_path):
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.0"
    (tmp_path / "pyvenv.cfg").write_text(
        f"home = /usr/bin\ninclude-system-site-packages = false\nversion = {ver}\n"
    )
    assert _venv_matches_system_python(str(tmp_path)) is True


def test_venv_matches_false_for_different_python(tmp_path):
    # A version that cannot equal the running interpreter's X.Y (project is 3.12+).
    (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 2.7.18\n")
    assert _venv_matches_system_python(str(tmp_path)) is False


def test_venv_matches_ignores_version_prefix_collision(tmp_path):
    # "3.1" must not match a "3.1x" interpreter via a bare startswith.
    major, minor = sys.version_info.major, sys.version_info.minor
    (tmp_path / "pyvenv.cfg").write_text(f"version = {major}.{minor}9.0\n")
    assert _venv_matches_system_python(str(tmp_path)) is False
