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
import secrets
import subprocess
from pathlib import Path
from typing import Optional

import typer

DEFAULT_MOUNT = "/data"

app = typer.Typer(
    name="luks-setup",
    help="LUKS2 encryption helper for benchmark VM storage disks.",
    no_args_is_help=True,
)


_STORAGE_LABEL = "storage"


def _find_storage_device() -> str:
    """Auto-detect the storage block device.

    Looks up /dev/disk/by-label/storage first (the label applied by create-cache.sh
    on the host, and preserved on the LUKS header after luks-setup encrypts the
    volume). Falls back to positional detection if the label symlink is absent.
    """
    by_label = Path(f"/dev/disk/by-label/{_STORAGE_LABEL}")
    if by_label.is_symlink():
        return os.path.realpath(by_label)

    # Fallback: first virtio disk that is not the config volume.
    config_dev = subprocess.run(
        ["blkid", "-l", "-o", "device", "-t", "LABEL=tdx-config"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()

    for dev in ("/dev/vdb", "/dev/vdc", "/dev/vdd", "/dev/vde"):
        if not Path(dev).is_block_device():
            continue
        if config_dev and os.path.realpath(dev) == os.path.realpath(config_dev):
            continue
        return dev

    raise RuntimeError(
        f"Could not auto-detect storage block device "
        f"(label '{_STORAGE_LABEL}' not found). "
        "Pass the device path explicitly: luks-setup setup /dev/vdX"
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


def _generate_passphrase() -> str:
    """Return a 128-character cryptographically secure hex passphrase."""
    return secrets.token_hex(64)


def _run(cmd: list[str], check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
    """Run a command, streaming output to the terminal.

    Pass ``stdin`` to feed text to the process's standard input without
    exposing it on the command line (e.g. for cryptsetup --key-file=-).
    """
    return subprocess.run(cmd, check=check, input=stdin, text=(stdin is not None))


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


def _get_mount_point(device: str) -> str | None:
    """Return the mount point of a device, or None if not mounted."""
    real = os.path.realpath(device)
    result = subprocess.run(
        ["findmnt", "-n", "-o", "TARGET", "--source", real],
        capture_output=True,
        text=True,
        check=False,
    )
    mount = result.stdout.strip()
    return mount if mount else None


def _unmount_if_needed(device: str) -> None:
    """Unmount the device if it is currently mounted."""
    mount = _get_mount_point(device)
    if mount:
        typer.echo(f"Device is mounted at {mount} — unmounting before encryption...")
        subprocess.run(["umount", mount], check=True)
        typer.echo(f"Unmounted {mount}")


@app.command()
def setup(
    device: Optional[str] = typer.Argument(None, help="Block device to encrypt (default: auto-detected)"),
    mount_point: Optional[str] = typer.Argument(None, help=f"Where to mount the encrypted volume (default: {DEFAULT_MOUNT})"),
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

    The storage device is auto-detected if not specified. Mounts at /data by default.

    The volume is mounted for the current session only. No entries are written
    to /etc/crypttab or /etc/fstab — after a reboot, use `luks-setup open`
    to unlock and mount the volume again.

    WARNING: all data on the device will be destroyed.
    """
    if device is None:
        try:
            device = _find_storage_device()
            typer.echo(f"Auto-detected storage device: {device}")
        except RuntimeError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)
    if mount_point is None:
        mount_point = DEFAULT_MOUNT
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
        typer.echo(f"\nWarning: {device} contains an existing filesystem (likely the unencrypted")
        typer.echo("storage volume). It will be wiped and replaced with a LUKS2 container.\n")
        confirmed = typer.confirm("Continue?", default=False)
        if not confirmed:
            typer.echo("Aborted.")
            raise typer.Exit(0)

    # Generate a cryptographically secure passphrase and present it before any
    # destructive operation so the user can record it first.
    passphrase = _generate_passphrase()
    typer.echo("\n" + "=" * 72)
    typer.echo("  ENCRYPTION PASSPHRASE — SAVE THIS NOW")
    typer.echo("=" * 72)
    typer.echo(f"\n  {passphrase}\n")
    typer.echo("  This is a 128-character cryptographically secure hex passphrase.")
    typer.echo("  It will NOT be stored anywhere on this system.")
    typer.echo("  You will need it every time you unlock the volume after a reboot.")
    typer.echo("  Store it in a password manager or other secure location before continuing.")
    typer.echo("\n" + "=" * 72 + "\n")
    typer.confirm("I have saved the passphrase and am ready to proceed", abort=True)

    _unmount_if_needed(device)

    typer.echo(f"\n[1/5] Wiping {device}...")
    _run(["wipefs", "-a", device])

    typer.echo(f"\n[2/5] Creating LUKS2 container on {device}...")
    _run(
        ["cryptsetup", "luksFormat", "--type", "luks2", "--label", label, "--batch-mode", "--key-file=-", device],
        stdin=passphrase,
    )

    typer.echo(f"\n[3/5] Opening LUKS container as '{dm_name}'...")
    _run(["cryptsetup", "luksOpen", "--key-file=-", device, dm_name], stdin=passphrase)

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
  luks-setup open

To verify encryption: cryptsetup status {dm_name}
""")


@app.command()
def open(
    device: Optional[str] = typer.Argument(None, help="Encrypted block device (default: auto-detected)"),
    mount_point: Optional[str] = typer.Argument(None, help=f"Where to mount the volume (default: {DEFAULT_MOUNT})"),
    dm_name: str = typer.Option("", "--name", "-n", help="Device-mapper name (defaults to device basename)"),
    key_file: str = typer.Option("", "--key-file", help="Path to key file (omit for interactive passphrase)"),
) -> None:
    """
    Open and mount a previously encrypted device.

    The storage device is auto-detected if not specified. Mounts at /data by default.

    Run this after each reboot to unlock and mount the volume.
    """
    if device is None:
        try:
            device = _find_storage_device()
            typer.echo(f"Auto-detected storage device: {device}")
        except RuntimeError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)
    if mount_point is None:
        mount_point = DEFAULT_MOUNT
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
