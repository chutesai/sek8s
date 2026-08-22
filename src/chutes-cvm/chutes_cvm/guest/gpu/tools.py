"""NVIDIA GPU admin tools installer.

Ensures nvidia-gpu-tools CLI is available, installing from a bundled wheel
into a venv if necessary.
"""

import os
import subprocess
import sys

from chutes_cvm.paths import gpu_tools_dir


def _cli_healthy() -> bool:
    """Return True if nvidia-gpu-tools is on PATH and actually executes.

    Presence on PATH is not sufficient: /usr/local/bin/nvidia-gpu-tools is a
    symlink into a venv whose interpreter and site-packages are bound to one
    Python minor version. An OS upgrade that bumps the system Python (e.g.
    25.10 -> 26.04, 3.13 -> 3.14) leaves the symlink resolving but the wheel's
    modules unreachable, so the CLI raises ModuleNotFoundError. Verify it runs
    (``--help`` exits 0) rather than trusting ``which``.
    """
    which = subprocess.run(["which", "nvidia-gpu-tools"], capture_output=True)
    if which.returncode != 0:
        return False
    try:
        probe = subprocess.run(
            ["nvidia-gpu-tools", "--help"], capture_output=True, timeout=15
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return probe.returncode == 0


def _venv_matches_system_python(venv_dir: str) -> bool:
    """Return True if the venv was built for the running Python minor version.

    Compares pyvenv.cfg's ``version`` to the current interpreter's ``X.Y``. A
    mismatch means the system Python was upgraded and the venv's version-scoped
    ``lib/pythonX.Y/site-packages`` are no longer importable, so it must be
    rebuilt rather than reused.
    """
    cfg = os.path.join(venv_dir, "pyvenv.cfg")
    if not os.path.exists(cfg):
        return False
    try:
        with open(cfg) as fh:
            content = fh.read()
    except OSError:
        return False
    target = f"{sys.version_info.major}.{sys.version_info.minor}"
    for line in content.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "version":
            value = value.strip()
            return value == target or value.startswith(target + ".")
    return False


def ensure_gpu_tools_available() -> str:
    """Ensure nvidia-gpu-tools CLI is available and functional.

    Returns early only when the installed CLI actually runs — an OS upgrade can
    bump the system Python and orphan the venv, leaving the CLI on PATH but
    broken. Otherwise (re)installs from the bundled wheel into a venv rebuilt
    for the current Python and creates a system-wide symlink.

    Returns:
        Command string to use for nvidia-gpu-tools.

    Raises:
        FileNotFoundError: If bundled wheel file is not found.
        RuntimeError: If python3 is not available or installation fails.
        subprocess.CalledProcessError: If installation fails.
    """
    if _cli_healthy():
        return "nvidia-gpu-tools"

    result = subprocess.run(["which", "python3"], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            "python3 is not available. Please install python3 to install GPU admin tools."
        )

    result = subprocess.run(["python3", "-m", "venv", "--help"], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            "The python3-venv package is not installed. "
            "Please install it using: sudo apt install python3-venv\n"
            "For Python 3.13 specifically: sudo apt install python3.13-venv\n"
            "After installing, the script will automatically create a virtual environment "
            "and install the GPU admin tools."
        )

    bundled_tools_dir = str(gpu_tools_dir())
    if not os.path.exists(bundled_tools_dir):
        raise FileNotFoundError(
            f"GPU tools directory not found: {bundled_tools_dir}. "
            "Expected a .whl file to be committed to the repository."
        )

    wheel_files = [f for f in os.listdir(bundled_tools_dir) if f.endswith(".whl")]
    if not wheel_files:
        raise FileNotFoundError(
            f"No bundled GPU tools wheel found in {bundled_tools_dir}. "
            "Expected a .whl file to be committed to the repository."
        )

    wheel_file = os.path.join(bundled_tools_dir, wheel_files[0])
    venv_dir = os.path.join(bundled_tools_dir, "venv")
    venv_python = os.path.join(venv_dir, "bin", "python")
    venv_pip = os.path.join(venv_dir, "bin", "pip")
    venv_bin = os.path.join(venv_dir, "bin")
    cli_symlink = "/usr/local/bin/nvidia-gpu-tools"

    def _create_venv() -> None:
        print("  Creating virtual environment for GPU admin tools...")
        try:
            subprocess.check_call(
                ["sudo", "python3", "-m", "venv", venv_dir],
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to create virtual environment: {e}\n"
                "The python3-venv package may not be installed. "
                "Please install it using: sudo apt install python3-venv\n"
                "For Python 3.13 specifically: sudo apt install python3.13-venv"
            )

    # A venv is bound to one Python minor version (its packages live under
    # lib/pythonX.Y/site-packages). If the system Python was upgraded, the venv
    # is present but its packages are unreachable — tear it down so it rebuilds
    # clean rather than reinstalling the wheel into a stale tree.
    if os.path.exists(venv_dir) and not _venv_matches_system_python(venv_dir):
        print("  GPU tools venv was built for a different Python — recreating...")
        subprocess.check_call(["sudo", "rm", "-rf", venv_dir])

    if not os.path.exists(venv_dir):
        _create_venv()

    if not os.path.exists(venv_pip):
        print("  Bootstrapping pip in virtual environment...")
        try:
            subprocess.check_call(
                ["sudo", venv_python, "-m", "ensurepip", "--upgrade"],
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError:
            print(
                "  Stale virtual environment detected (ensurepip unavailable) — recreating..."
            )
            subprocess.check_call(["sudo", "rm", "-rf", venv_dir])
            _create_venv()
            # If pip still isn't present after a clean recreate, the venv package is broken
            if not os.path.exists(venv_pip):
                subprocess.check_call(
                    ["sudo", venv_python, "-m", "ensurepip", "--upgrade"],
                    stderr=subprocess.STDOUT,
                )

    print(
        f"  Installing GPU admin tools from bundled wheel: {os.path.basename(wheel_file)}"
    )
    subprocess.check_call(
        ["sudo", venv_pip, "install", "--quiet", "--upgrade", wheel_file]
    )

    cli_in_venv = os.path.join(venv_bin, "nvidia-gpu-tools")

    if not os.path.exists(cli_in_venv):
        raise RuntimeError(
            "nvidia-gpu-tools CLI not found in venv after installation. "
            "The wheel may not have installed correctly or the entry point is misconfigured."
        )

    test_result = subprocess.run(
        [cli_in_venv, "--help"], capture_output=True, timeout=5
    )
    if test_result.returncode != 0:
        error_msg = (
            test_result.stderr.decode() if test_result.stderr else "Unknown error"
        )
        raise RuntimeError(
            f"nvidia-gpu-tools CLI entry point is broken. "
            f"The wheel was not built correctly. Error: {error_msg}\n"
            f"Please rebuild the wheel using: cd {bundled_tools_dir} && ./bundle-tools.sh"
        )

    # lexists (not exists) so a dangling symlink — left behind when the venv it
    # pointed into was torn down as stale — is still removed before relinking.
    if os.path.lexists(cli_symlink):
        if os.path.islink(cli_symlink):
            subprocess.check_call(["sudo", "rm", cli_symlink])
        else:
            raise RuntimeError(
                f"Cannot create symlink: {cli_symlink} exists and is not a symlink. "
                "Please remove it manually and try again."
            )

    print(f"  Creating system-wide symlink: {cli_symlink}")
    subprocess.check_call(["sudo", "ln", "-s", cli_in_venv, cli_symlink])

    result = subprocess.run(["which", "nvidia-gpu-tools"], capture_output=True)
    if result.returncode == 0:
        return "nvidia-gpu-tools"
    else:
        raise RuntimeError(
            "nvidia-gpu-tools installation succeeded but CLI not found in PATH. "
            f"Symlink created at {cli_symlink}, but it may not be in your PATH."
        )
