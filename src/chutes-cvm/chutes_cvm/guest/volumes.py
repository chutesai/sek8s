"""Materializing a guest's volumes: cache, storage and the signed config disk.

Stage 3 of a launch. Idempotent: each is an ``ensure`` -- a volume that already exists at the
right size is left alone, so a relaunch does not rebuild a multi-terabyte cache.

The paths these resolve to become the ``GuestVolumes`` the command attaches, and the ORDER the
orchestrator calls them in assigns their pcie.0 slots -- which land in the DSDT and so in RTMR0.
"""

import os

from chutes_cvm.guest.config import LaunchConfig
from chutes_cvm.guest.privileged import LaunchError, run
from chutes_cvm.paths import SCRIPTS_DIR


def volume_path(vol: str) -> str:
    """Resolve a (possibly relative) volume path the way the bash helper will — relative names
    live in the scripts working directory (SCRIPTS_DIR), matching the former quick-launch cwd.
    """
    return vol if os.path.isabs(vol) else str(SCRIPTS_DIR / vol)


def ensure_raw_volume(vol: str, size: str, label: str, kind: str) -> None:
    """Create a raw LUKS volume via volumes/create-cache.sh unless it already exists.

    ``kind`` is only for messages. qcow2 volumes are never created (only reused if present).
    """
    path = volume_path(vol)
    if os.path.exists(path):
        print(f"✓ Using existing {kind} volume: {vol}")
        return
    if vol.endswith(".qcow2"):
        raise LaunchError(
            f"qcow2 volumes cannot be created — use .raw for a new {kind} volume "
            f"(e.g. {kind}-<hostname>.raw). Existing qcow2 volumes are reused if present."
        )
    print(f"Creating {kind} volume at: {vol} ({size})")
    run([str(SCRIPTS_DIR / "volumes" / "create-cache.sh"), vol, size, label])


def setup_config_volume(config: LaunchConfig, benchmark: bool) -> None:
    """Create/refresh the config volume via volumes/create-config.sh.

    Benchmark passes hostname + network positionally with empty miner creds; production passes
    every value by NAME through the environment (create-config.sh reads those), so long/optional
    fields (docker creds, operator key) stay off the command line.
    """
    vol = config.volumes.config.path
    action = "Refreshing existing" if os.path.exists(volume_path(vol)) else "Creating"
    print(f"{action} config volume: {vol}")
    gateway = config.network.bridge_ip.split("/")[0]
    script = str(SCRIPTS_DIR / "volumes" / "create-config.sh")
    if benchmark:
        run(
            [
                "sudo",
                script,
                vol,
                config.vm.hostname,
                "",
                "",
                config.network.vm_ip,
                gateway,
                config.network.dns,
            ]
        )
    else:
        run(
            [
                "sudo",
                f"HOSTNAME={config.vm.hostname}",
                f"MINER_SS58={config.miner.ss58}",
                f"MINER_SEED={config.miner.seed}",
                f"VM_IP={config.network.vm_ip}",
                f"VM_GATEWAY={gateway}",
                f"VM_DNS={config.network.dns}",
                f"DOCKER_HUB_USER={config.docker_hub.username}",
                f"DOCKER_HUB_TOKEN={config.docker_hub.token}",
                script,
                vol,
            ]
        )
