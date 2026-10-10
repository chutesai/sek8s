"""teardown.sh stops when the VM cannot be force-stopped, instead of tearing the environment down
around a QEMU that still holds the devices and the image's write lock (e.g. mid-reclaim).

`chutes-cvm`, `ip`, `pgrep` and `systemctl` are stubbed, so nothing on the host is touched.
"""

import pytest

SCRIPT = "src/chutes-cvm/chutes_cvm/scripts/teardown.sh"


@pytest.fixture
def run_teardown(shell):
    shell.stub("ip", "exit 0\n")
    shell.stub("pgrep", "exit 1\n")  # no VM processes left
    shell.stub("systemctl", "exit 3\n")  # benchmark-netlog not active

    def _run(stop_exit, stop_stderr=""):
        shell.stub(
            "chutes-cvm",
            f'echo "chutes-cvm $*" >> "{shell.tmp}/calls"\n'
            f'echo "{stop_stderr}" >&2\n'
            f"exit {stop_exit}\n",
        )
        result = shell.run(SCRIPT, env={"PUBLIC_IFACE": ""})
        calls = (shell.tmp / "calls").read_text().splitlines()
        return result, calls

    return _run


def test_a_vm_that_survives_the_force_stop_stops_the_teardown(run_teardown):
    result, calls = run_teardown(1, stop_stderr="QEMU is still reclaiming the guest's memory")

    assert calls == ["chutes-cvm guest stop --force"]
    assert result.returncode == 1
    assert "still reclaiming" in result.stderr  # the stop's own reason is no longer hidden
    assert "could not be stopped" in result.stderr
    assert "Teardown complete" not in result.stdout


def test_a_stopped_vm_tears_the_environment_down(run_teardown):
    result, calls = run_teardown(0)

    assert calls == ["chutes-cvm guest stop --force"]
    assert result.returncode == 0, result.stderr
    assert "Teardown complete" in result.stdout
