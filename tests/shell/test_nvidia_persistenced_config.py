"""nvidia-persistenced-config must never write into the RTMR3-measured tree.

The bug these guard against shipped in 1.4.0. The script generated
/etc/systemd/system/nvidia-persistenced.service.d/override.conf at boot, and 1.4.0 was
also the release that added /etc/systemd/system to the measured path list. The boot-time
gate only hash-checks paths already present in the build manifest, so a brand-new path
passed silently while still changing the chain digest: every VM attested one RTMR3 on
its first boot and a different one on every boot after, with two fleet-wide variants
because the generated content depended on NVSwitch presence.

The fix splits the two halves. The drop-in is static and baked into the image, so the
manifest covers it; only the flag varies, and it travels through /run, which is tmpfs
and outside the measured set. Both halves are pinned here -- reuniting them reintroduces
a silent, fleet-wide attestation failure that no unit test other than these would catch.
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT_REL = "ansible/guest/roles/gpu/files/nvidia-persistenced-config.sh"
SCRIPT = REPO / SCRIPT_REL
DROPIN = REPO / "ansible/guest/roles/gpu/files/nvidia-persistenced-dropin.conf"
DEVICE_SETUP = REPO / "ansible/guest/roles/gpu/tasks/device-setup.yml"

# Directory roots from tdx-measure-miner.conf that a boot-time script could plausibly
# write into. Writing under any of these moves RTMR3.
MEASURED_ROOTS = (
    "/etc/systemd/system",
    "/usr/lib/systemd/system",
    "/usr/local/bin",
    "/usr/local/sbin",
    "/usr/local/lib",
    "/etc/modprobe.d",
    "/etc/sysctl.d",
    "/etc/security",
)


@pytest.fixture(scope="module")
def script_text() -> str:
    return SCRIPT.read_text()


@pytest.fixture
def run_script(shell):
    """Run the config script with lspci/logger stubbed and the env file redirected."""

    def _run(*, nvswitch: bool):
        shell.stub(
            "lspci",
            'echo "0f:00.0 Bridge [0680]: NVIDIA Corporation GH100 [10de:2342]"\n'
            + (
                'echo "10:00.0 Bridge [0680]: NVIDIA Corporation H100 NVSwitch [10de:22a3]"\n'
                if nvswitch
                else ""
            ),
        )
        shell.stub("logger", "exit 0")
        env_file = shell.tmp / "run" / "nvidia-persistenced-mode.env"
        result = shell.run(SCRIPT_REL, env={"NVPD_ENV_FILE": str(env_file)})
        return result, env_file

    return _run


def test_nvswitch_host_selects_uvm_persistence(run_script):
    result, env_file = run_script(nvswitch=True)
    assert result.returncode == 0, result.stderr
    assert env_file.read_text() == "NVPD_FLAG=--uvm-persistence-mode\n"


def test_non_nvswitch_host_selects_plain_persistence(run_script):
    result, env_file = run_script(nvswitch=False)
    assert result.returncode == 0, result.stderr
    assert env_file.read_text() == "NVPD_FLAG=--persistence-mode\n"


def test_the_env_file_is_the_only_thing_written(run_script, shell):
    """No temp file survives, so nothing under /run drifts either -- and, more to the
    point, the script's whole output is one file whose path the caller controls."""
    result, env_file = run_script(nvswitch=True)
    assert result.returncode == 0, result.stderr
    written = sorted(
        p
        for p in shell.tmp.rglob("*")
        if p.is_file() and shell.bin not in p.parents  # the harness's own stubs
    )
    assert written == [env_file], f"unexpected files written: {written}"


def test_script_never_targets_a_measured_path(script_text):
    """A text guard, because the damage is done at build/boot time on a real root where
    a test cannot observe it."""
    body = "\n".join(
        line for line in script_text.splitlines() if not line.lstrip().startswith("#")
    )
    for root in MEASURED_ROOTS:
        assert root not in body, (
            f"{SCRIPT.name} references the RTMR3-measured path {root}; writing there at "
            "boot changes RTMR3 on every subsequent boot and fails attestation fleet-wide"
        )
    assert "/run/nvidia-persistenced-mode.env" in body


def test_dropin_is_static_and_reads_the_flag_from_run():
    text = DROPIN.read_text()
    assert "ExecStart=/usr/bin/nvidia-persistenced $NVPD_FLAG --verbose" in text
    assert "EnvironmentFile=-/run/nvidia-persistenced-mode.env" in text

    # A condition-skipped nvidia-persistenced-config still satisfies Requires=, so the
    # env file can be absent; the default must be in place before the file overrides it.
    lines = [line.strip() for line in text.splitlines()]
    default_at = lines.index("Environment=NVPD_FLAG=--persistence-mode")
    envfile_at = lines.index("EnvironmentFile=-/run/nvidia-persistenced-mode.env")
    assert default_at < envfile_at, (
        "EnvironmentFile= precedes the Environment= default, so a host without "
        "/dev/nvidia0 would start nvidia-persistenced with an empty flag"
    )


def test_dropin_is_installed_at_build_time():
    """It must exist in the image, or the build manifest will not cover it and the
    measured tree gains an unknown path the first time anything creates it."""
    tasks = DEVICE_SETUP.read_text()
    assert "src: nvidia-persistenced-dropin.conf" in tasks
    assert (
        "dest: /etc/systemd/system/nvidia-persistenced.service.d/override.conf" in tasks
    )
