"""The config volume carries the miner ss58 plus exactly one key.

setup_config_volume hands both key slots to create-config.sh by name (the config model has already
refused both being set); create-config.sh writes the one that is set and refuses ambiguous or
partial credentials itself. Its argument checks run before any privileged step, so they are
exercised here; the qemu-nbd/mkfs writes need root and a real device.
"""

import subprocess
from unittest.mock import patch

import hotkey_fixtures as hk
import pytest
from chutes_cvm.guest.config import LaunchConfig
from chutes_cvm.guest.volumes import setup_config_volume
from chutes_cvm.paths import SCRIPTS_DIR

CREATE_CONFIG = SCRIPTS_DIR / "volumes" / "create-config.sh"


def _config(**miner) -> LaunchConfig:
    return LaunchConfig.from_file(
        None, vm={"hostname": "h"}, miner={"ss58": hk.SS58, **miner}
    )


@pytest.mark.parametrize(
    "miner, private_key, seed",
    [
        ({"private_key": hk.PRIVATE_KEY}, hk.PRIVATE_KEY, ""),
        ({"seed": hk.SEED}, "", hk.SEED),
    ],
    ids=["private-key", "seed"],
)
def test_setup_config_volume_passes_the_one_key(miner, private_key, seed):
    with patch("chutes_cvm.guest.volumes.run") as run:
        setup_config_volume(_config(**miner), benchmark=False)

    argv = run.call_args.args[0]
    assert f"MINER_SS58={hk.SS58}" in argv
    assert f"MINER_PRIVATE_KEY={private_key}" in argv
    assert f"MINER_SEED={seed}" in argv


@pytest.mark.parametrize(
    "env, complaint",
    [
        (
            {
                "MINER_SS58": hk.SS58,
                "MINER_PRIVATE_KEY": hk.PRIVATE_KEY,
                "MINER_SEED": hk.SEED,
            },
            "not both",
        ),
        ({"MINER_SS58": hk.SS58}, "must both be provided"),
        ({"MINER_PRIVATE_KEY": hk.PRIVATE_KEY}, "must both be provided"),
    ],
    ids=["both-keys", "ss58-without-key", "key-without-ss58"],
)
def test_create_config_refuses_ambiguous_or_partial_credentials(
    tmp_path, env, complaint
):
    result = subprocess.run(
        ["bash", str(CREATE_CONFIG), str(tmp_path / "config.qcow2")],
        env={
            "PATH": "/usr/bin:/bin",
            "HOSTNAME": "h",
            "VM_IP": "192.168.100.2",
            "VM_GATEWAY": "192.168.100.1",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert complaint in result.stdout + result.stderr
    assert not (tmp_path / "config.qcow2").exists()
