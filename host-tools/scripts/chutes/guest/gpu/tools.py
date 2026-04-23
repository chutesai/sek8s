"""NVIDIA GPU admin tools installer.

Ensures nvidia-gpu-tools CLI is available, installing from a bundled wheel
into a venv if necessary.
"""

import os
import subprocess


def _scripts_dir() -> str:
    """Return the host-tools/scripts/ directory (parent of the chutes.guest package)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def ensure_gpu_tools_available() -> str:
    """Ensure nvidia-gpu-tools CLI is available.

    Checks if nvidia-gpu-tools is in PATH. If not, installs from bundled
    wheel into a venv and creates a system-wide symlink.

    Returns:
        Command string to use for nvidia-gpu-tools.

    Raises:
        FileNotFoundError: If bundled wheel file is not found.
        RuntimeError: If python3 is not available or installation fails.
        subprocess.CalledProcessError: If installation fails.
    """
    result = subprocess.run(['which', 'nvidia-gpu-tools'], capture_output=True)
    if result.returncode == 0:
        return 'nvidia-gpu-tools'

    result = subprocess.run(['which', 'python3'], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            "python3 is not available. Please install python3 to install GPU admin tools."
        )

    result = subprocess.run(
        ['python3', '-m', 'venv', '--help'], capture_output=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            "The python3-venv package is not installed. "
            "Please install it using: sudo apt install python3-venv\n"
            "For Python 3.13 specifically: sudo apt install python3.13-venv\n"
            "After installing, the script will automatically create a virtual environment "
            "and install the GPU admin tools."
        )

    bundled_tools_dir = os.path.join(_scripts_dir(), 'gpu-tools')
    if not os.path.exists(bundled_tools_dir):
        raise FileNotFoundError(
            f"GPU tools directory not found: {bundled_tools_dir}. "
            "Expected a .whl file to be committed to the repository."
        )

    wheel_files = [f for f in os.listdir(bundled_tools_dir) if f.endswith('.whl')]
    if not wheel_files:
        raise FileNotFoundError(
            f"No bundled GPU tools wheel found in {bundled_tools_dir}. "
            "Expected a .whl file to be committed to the repository."
        )

    wheel_file = os.path.join(bundled_tools_dir, wheel_files[0])
    venv_dir = os.path.join(bundled_tools_dir, 'venv')
    venv_python = os.path.join(venv_dir, 'bin', 'python')
    venv_pip = os.path.join(venv_dir, 'bin', 'pip')
    venv_bin = os.path.join(venv_dir, 'bin')
    cli_symlink = '/usr/local/bin/nvidia-gpu-tools'

    def _create_venv() -> None:
        print('  Creating virtual environment for GPU admin tools...')
        try:
            subprocess.check_call(
                ['sudo', 'python3', '-m', 'venv', venv_dir],
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to create virtual environment: {e}\n"
                "The python3-venv package may not be installed. "
                "Please install it using: sudo apt install python3-venv\n"
                "For Python 3.13 specifically: sudo apt install python3.13-venv"
            )

    if not os.path.exists(venv_dir):
        _create_venv()

    if not os.path.exists(venv_pip):
        print('  Bootstrapping pip in virtual environment...')
        try:
            subprocess.check_call(
                ['sudo', venv_python, '-m', 'ensurepip', '--upgrade'],
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError:
            print('  Stale virtual environment detected (ensurepip unavailable) — recreating...')
            subprocess.check_call(['sudo', 'rm', '-rf', venv_dir])
            _create_venv()
            # If pip still isn't present after a clean recreate, the venv package is broken
            if not os.path.exists(venv_pip):
                subprocess.check_call(
                    ['sudo', venv_python, '-m', 'ensurepip', '--upgrade'],
                    stderr=subprocess.STDOUT,
                )

    print(f'  Installing GPU admin tools from bundled wheel: {os.path.basename(wheel_file)}')
    subprocess.check_call(
        ['sudo', venv_pip, 'install', '--quiet', '--upgrade', wheel_file]
    )

    cli_in_venv = os.path.join(venv_bin, 'nvidia-gpu-tools')

    if not os.path.exists(cli_in_venv):
        raise RuntimeError(
            "nvidia-gpu-tools CLI not found in venv after installation. "
            "The wheel may not have installed correctly or the entry point is misconfigured."
        )

    test_result = subprocess.run(
        [cli_in_venv, '--help'], capture_output=True, timeout=5
    )
    if test_result.returncode != 0:
        error_msg = test_result.stderr.decode() if test_result.stderr else "Unknown error"
        raise RuntimeError(
            f"nvidia-gpu-tools CLI entry point is broken. "
            f"The wheel was not built correctly. Error: {error_msg}\n"
            f"Please rebuild the wheel using: cd {bundled_tools_dir} && ./bundle-tools.sh"
        )

    if os.path.exists(cli_symlink):
        if os.path.islink(cli_symlink):
            subprocess.check_call(['sudo', 'rm', cli_symlink])
        else:
            raise RuntimeError(
                f"Cannot create symlink: {cli_symlink} exists and is not a symlink. "
                "Please remove it manually and try again."
            )

    print(f'  Creating system-wide symlink: {cli_symlink}')
    subprocess.check_call(['sudo', 'ln', '-s', cli_in_venv, cli_symlink])

    result = subprocess.run(['which', 'nvidia-gpu-tools'], capture_output=True)
    if result.returncode == 0:
        return 'nvidia-gpu-tools'
    else:
        raise RuntimeError(
            "nvidia-gpu-tools installation succeeded but CLI not found in PATH. "
            f"Symlink created at {cli_symlink}, but it may not be in your PATH."
        )
