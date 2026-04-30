#!/opt/chutes-nvevidence/venv/bin/python
"""
luks-setup -- LUKS2 encryption helper for benchmark VM storage disks.

Provides two commands:
  setup  Wipe, encrypt, format, and mount a device for the current session.
  open   Open and mount a previously encrypted device.

Intentionally does NOT persist to /etc/crypttab or /etc/fstab. The volume
must be explicitly unlocked via `luks-setup open` after each reboot, ensuring
only parties with the passphrase can access the data.
"""

import os
import subprocess
from pathlib import Path

import typer

app = typer.Typer(
    name="luks-setup",
    help="LUKS2 encryption helper for benchmark VM storage disks.",
    no_args_is_help=True,
)


def _require_root() -> None:
    if os.geteuid() != 0:
        typer.echo("Error: luks-setup must be run as root.", err=True)
        raise typer.Exit(1)


def _require_block_device(device: str) -> None:
    p = Path(device)
    if not p.exists():
        typer.echo(f"Error: device {device} does not exist.", err=True)
        raise typer.Exit(1)
    if not p.is_block_device():
        typer.echo(f"Error: {device} is not a block device.", err=True)
        raise typer.Exit(1)


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, streaming output to the terminal."""
    return subprocess.run(cmd, check=check)


def _get_device_uuid(device: str) -> str:
    result = subprocess.run(
        ["blkid", "-s", "UUID", "-o", "value", device],
        capture_output=True,
        text=True,
        check=True,
    )
    uuid = result.stdout.strip()
    if not uuid:
        raise RuntimeError(f"Could not determine UUID for {device}")
    return uuid


def _is_luks(device: str) -> bool:
    result = subprocess.run(
        ["cryptsetup", "isLuks", device],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _device_has_data(device: str) -> bool:
    result = subprocess.run(
        ["blkid", device],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


@app.command()
def setup(
    device: str = typer.Argument(..., help="Block device to encrypt (e.g. /dev/vdb)"),
    mount_point: str = typer.Argument(..., help="Where to mount the encrypted volume (e.g. /data)"),
    label: str = typer.Option("storage", "--label", "-l", help="Filesystem and LUKS header label"),
    dm_name: str = typer.Option(
        "",
        "--name",
        "-n",
        help="Device-mapper name (defaults to the label)",
    ),
    fs: str = typer.Option("xfs", "--fs", help="Filesystem to create: xfs or ext4"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """
    One-time setup: wipe, LUKS2-encrypt, format, and mount a device.

    The volume is mounted for the current session only. No entries are written
    to /etc/crypttab or /etc/fstab — after a reboot, use `luks-setup open`
    to unlock and mount the volume again.

    WARNING: all data on the device will be destroyed.
    """
    _require_root()
    _require_block_device(device)

    if not dm_name:
        dm_name = label

    if fs not in ("xfs", "ext4"):
        typer.echo(f"Error: unsupported filesystem '{fs}'. Use 'xfs' or 'ext4'.", err=True)
        raise typer.Exit(1)

    mapper_dev = f"/dev/mapper/{dm_name}"

    # Safety check
    if _device_has_data(device) and not yes:
        typer.echo(f"\nWarning: {device} appears to contain existing data.")
        typer.echo("ALL DATA WILL BE PERMANENTLY DESTROYED.\n")
        confirmed = typer.confirm("Continue?", default=False)
        if not confirmed:
            typer.echo("Aborted.")
            raise typer.Exit(0)

    typer.echo(f"\n[1/5] Wiping {device}...")
    _run(["wipefs", "-a", device])

    typer.echo(f"\n[2/5] Creating LUKS2 container on {device}...")
    typer.echo("      You will be prompted to enter and confirm a passphrase.")
    _run(["cryptsetup", "luksFormat", "--type", "luks2", "--label", label, device])

    typer.echo(f"\n[3/5] Opening LUKS container as '{dm_name}'...")
    typer.echo("      Enter the passphrase you just set.")
    _run(["cryptsetup", "luksOpen", device, dm_name])

    typer.echo(f"\n[4/5] Creating {fs} filesystem on {mapper_dev}...")
    if fs == "xfs":
        _run(["mkfs.xfs", "-L", label, mapper_dev])
    else:
        _run(["mkfs.ext4", "-L", label, mapper_dev])

    typer.echo(f"\n[5/5] Mounting {mapper_dev} at {mount_point}...")
    Path(mount_point).mkdir(parents=True, exist_ok=True)
    _run(["mount", mapper_dev, mount_point])

    uuid = _get_device_uuid(device)
    typer.echo(f"""
Setup complete.

  Device:       {device}
  UUID:         {uuid}
  LUKS label:   {label}
  Mapper name:  {dm_name}  (/dev/mapper/{dm_name})
  Filesystem:   {fs}
  Mount point:  {mount_point}

The volume is mounted for this session. After a reboot, unlock it with:
  luks-setup open {device} {mount_point}

To verify encryption: cryptsetup status {dm_name}
""")


@app.command()
def open(
    device: str = typer.Argument(..., help="Encrypted block device (e.g. /dev/vdb)"),
    mount_point: str = typer.Argument(..., help="Where to mount the volume"),
    dm_name: str = typer.Option("", "--name", "-n", help="Device-mapper name (defaults to device basename)"),
    key_file: str = typer.Option("", "--key-file", help="Path to key file (omit for interactive passphrase)"),
) -> None:
    """
    Open and mount a previously encrypted device.

    Run this after each reboot to unlock and mount the volume.
    """
    _require_root()
    _require_block_device(device)

    if not _is_luks(device):
        typer.echo(f"Error: {device} does not appear to be a LUKS-encrypted device.", err=True)
        raise typer.Exit(1)

    if not dm_name:
        dm_name = Path(device).name

    mapper_dev = f"/dev/mapper/{dm_name}"

    if Path(mapper_dev).exists():
        typer.echo(f"'{dm_name}' is already open at {mapper_dev}.")
    else:
        typer.echo(f"Opening {device} as '{dm_name}'...")
        cmd = ["cryptsetup", "luksOpen", device, dm_name]
        if key_file:
            cmd += ["--key-file", key_file]
        _run(cmd)

    typer.echo(f"Mounting {mapper_dev} at {mount_point}...")
    Path(mount_point).mkdir(parents=True, exist_ok=True)
    _run(["mount", mapper_dev, mount_point])
    typer.echo(f"Mounted. To verify: cryptsetup status {dm_name}")


if __name__ == "__main__":
    app()
