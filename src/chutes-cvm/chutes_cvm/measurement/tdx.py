"""Intel TDX measurements: MRTD and RTMR0-3.

``TdxMeasurements`` owns every TDX register of a release:

    MRTD + RTMR0  per host class. The tdx-measure fork self-generates all 15 RTMR0 events (no
                  CCEL) from the class's topology (``--platform-only --create-acpi-tables``,
                  QEMU in Docker); MRTD comes out of the same run.
    RTMR1/RTMR2   once per release: the staged direct-boot kernel/initrd/cmdline, through the
                  fork's ``--runtime-only`` mode. Post-LUKS: LUKS rebuilds the initrd, and RTMR2
                  measures that final one.
    RTMR3         once per release: the SHA-384 chain over the files the image's
                  /etc/tdx-measure.conf names. The root is mounted read-only (qemu-nbd, plus
                  cryptsetup for a LUKS root) and folded by ``rtmr3.compute_rtmr3``, the helper
                  the guest ships and runs itself.

Offline on any x86-64 Linux: no TDX, no GPUs. The RTMR3 mount needs root.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from chutes_cvm import proc
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.qemu import QemuCommand
from chutes_cvm.guest.tee import TdxTeeProvider
from chutes_cvm.measurement import rtmr3
from chutes_cvm.measurement.image_config import ImageConfig
from chutes_cvm.measurement.platform import (
    MeasurementError,
    PlatformMeasurements,
    staged_boot_artifacts,
)
from chutes_cvm.paths import GUEST_FIRMWARE, tdx_measure_script


class TdxMeasurements(PlatformMeasurements):
    """Intel TDX: RTMR0 per class from the topology it describes; MRTD and RTMR1-3 once.

    The tdx-measure fork computes MRTD and RTMR0 together (``--platform-only``) from a class's
    topology -- all 15 RTMR0 events, no CCEL -- so MRTD is read off those per-class runs.
    RTMR1-3 depend only on the image, so they are computed up front, before any slow fork run.
    """

    key = "tdx"
    provider = TdxTeeProvider

    def __init__(
        self, *, bios_dir: str, tdx_measure_bin: str, dist: str, image: str
    ) -> None:
        super().__init__()
        self._firmware = str(Path(bios_dir) / GUEST_FIRMWARE)
        self._tdx_measure_bin = tdx_measure_bin
        self._dist = dist
        self._mrtds: set[str] = set()
        rtmr1, rtmr2 = compute_rtmr1_2(image, tdx_measure_bin=tdx_measure_bin)
        rtmr3, _ = compute_rtmr3(
            image, luks_passphrase=os.environ.get("LUKS_PASSPHRASE")
        )
        print(
            f"    RTMR1={rtmr1[:16]}…  RTMR2={rtmr2[:16]}…  RTMR3={rtmr3[:16]}…",
            file=sys.stderr,
        )
        self._registers = {"rtmr1": rtmr1, "rtmr2": rtmr2, "rtmr3": rtmr3}

    def measure(self, host: HostProfile) -> dict:
        cmd = QemuCommand.for_measurement(host, firmware=self._firmware)
        with tempfile.TemporaryDirectory() as td:
            meta = ImageConfig(
                cmd, host, acpi_tables=str(Path(td) / "acpi.bin")
            ).to_dict()
            out = generate_acpi_blobs(
                meta, Path(td), tdx_measure_bin=self._tdx_measure_bin, dist=self._dist
            )
        self._mrtds.add(out.get("mrtd", "").upper())
        return {"rtmr0": (out.get("rtmr0") or "").upper()}

    @property
    def mrtd(self) -> str:
        """Version-level: one TDVF measures identically on every topology of a build. It comes
        from the fork runs, so it is empty in a release with no Intel classes."""
        if len(self._mrtds) > 1:
            raise ValueError(f"MRTD differs across topologies: {sorted(self._mrtds)}")
        return next(iter(self._mrtds), "")

    def section(self) -> dict:
        return {"mrtd": self.mrtd, **self._registers, "hardware": self.hardware}


# ── MRTD + RTMR0 (per host class) ───────────────────────────────────────────────────────────────


def generate_acpi_blobs(
    metadata: dict, out_dir: Path, *, tdx_measure_bin: str, dist: str
) -> dict:
    """Run `tdx-measure --create-acpi-tables` to dump the topology's fw_cfg ACPI
    blobs and its {mrtd, rtmr0}. Mirrors local/scripts/run_validation.sh:43. The
    generated etc/acpi/* land next to `acpi_tables` in the metadata; we read those
    for the #11-13 recompute. Returns the parsed tdx-measure JSON ({mrtd, rtmr0}).

    Runs OFFLINE on any x86-64 Linux with Docker + the fork — no TDX, no GPUs. KVM
    speeds the brief ACPI-gen QEMU run but isn't required; reserve=off (applied by
    image_config.ImageConfig) lifts the guest-sized-RAM requirement.

    Only the distribution is passed to --create-acpi-tables: the fork pins the exact
    QEMU source-package version *and* container image digest per dist (qemu_pkg_for),
    which is what makes the dump reproducible. The QEMU version label (e.g. "10.2.1", from the
    host profile) is a release label, NOT a Debian package version — forwarding it as the fork's
    version override lands an unresolvable `pull-lp-source qemu 10.2.1`.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "metadata.json"
    result_path = out_dir / "result.json"
    meta_path.write_text(json.dumps(metadata, indent=2))
    # The metadata positional is placed first: --create-acpi-tables is num_args=1..=2,
    # so a metadata path immediately after `dist` would be greedily eaten as the version.
    # Capture the output (the fork's docker build log is very noisy) and, on failure,
    # raise just the tail — the caller renders it as a one-line PENDING reason.
    result = proc.run(
        [
            tdx_measure_bin,
            str(meta_path),
            "--platform-only",
            "--json-file",
            str(result_path),
            "--create-acpi-tables",
            dist,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tail = "\n    ".join(
            (result.stderr or result.stdout or "").strip().splitlines()[-4:]
        )
        raise RuntimeError(
            f"tdx-measure --create-acpi-tables (dist={dist}) failed "
            f"(exit {result.returncode}):\n    {tail}"
        )
    return json.loads(result_path.read_text())


# ── RTMR1 / RTMR2 (per release) ─────────────────────────────────────────────────────────────────

_RTMR1_RE = re.compile(r"^RTMR1:\s*([0-9a-fA-F]+)", re.MULTILINE)
_RTMR2_RE = re.compile(r"^RTMR2:\s*([0-9a-fA-F]+)", re.MULTILINE)


def compute_rtmr1_2(
    image: str, tdx_measure_bin: str = "tdx-measure"
) -> tuple[str, str]:
    """Compute (RTMR1, RTMR2) from the image's staged direct-boot artifacts.

    Reads ``<image-without-ext>.{vmlinuz,initrd,cmdline}`` (staged by stage-boot-artifacts —
    the exact bytes the launcher boots) and runs the tdx-measure fork in ``--runtime-only``
    direct-boot mode. Returns bare uppercase hex. Needs the fork on PATH (or an absolute
    ``tdx_measure_bin``); no TDX/GPU/topology input.
    """
    kernel, initrd, cmdline = staged_boot_artifacts(image)

    metadata = {"direct": {"kernel": kernel, "initrd": initrd, "cmdline": cmdline}}
    with tempfile.TemporaryDirectory() as td:
        meta_path = os.path.join(td, "metadata.json")
        Path(meta_path).write_text(json.dumps(metadata))
        result = proc.run(
            [tdx_measure_bin, "--runtime-only", meta_path],
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-4:]
        raise MeasurementError(
            "tdx-measure --runtime-only failed "
            f"(exit {result.returncode}):\n    " + "\n    ".join(tail)
        )
    out = result.stdout
    m1, m2 = _RTMR1_RE.search(out), _RTMR2_RE.search(out)
    if not m1 or not m2:
        raise MeasurementError(
            "could not parse RTMR1/RTMR2 from tdx-measure output:\n" + out
        )
    return m1.group(1).upper(), m2.group(1).upper()


# ── RTMR3 (per release) ─────────────────────────────────────────────────────────────────────────


def _have(tool: str) -> bool:
    """True if ``tool`` is on PATH."""
    return shutil.which(tool) is not None


def _wait_for_path(path: str, timeout: float = 5.0) -> bool:
    """Poll for a device node to appear — partprobe populates ``/dev`` asynchronously."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return os.path.exists(path)


def _free_nbd_device() -> str:
    """The first ``/dev/nbdN`` with nothing attached (sysfs size 0)."""
    for i in range(16):
        try:
            if Path(f"/sys/block/nbd{i}/size").read_text().strip() == "0":
                return f"/dev/nbd{i}"
        except OSError:
            continue
    raise MeasurementError(
        "no free /dev/nbd device — run `modprobe nbd max_part=8` or disconnect a stale one"
    )


def _detect_root_partition(nbd: str) -> tuple[str, bool]:
    """``(root partition device, is_luks)`` for a connected nbd image.

    Mirrors the build's own blkid-based layout detection (luks_encrypt.yml): the root is the
    ``crypto_LUKS`` partition when the image is encrypted, else the largest ext4 (the separate
    ``/boot`` is a smaller ext4). blkid reads the on-disk signature directly, so detection never
    depends on libguestfs inspecting the image.
    """
    luks_part: str | None = None
    best_ext4: str | None = None
    best_size = -1
    for part in sorted(glob.glob(f"{nbd}p*")):
        fstype = proc.run(
            ["blkid", "-o", "value", "-s", "TYPE", part],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if fstype == "crypto_LUKS":
            luks_part = part
        elif fstype == "ext4":
            try:
                size = int(
                    Path(f"/sys/class/block/{os.path.basename(part)}/size").read_text()
                )
            except OSError:
                size = 0
            if size > best_size:
                best_size, best_ext4 = size, part
    if luks_part:
        return luks_part, True
    if best_ext4:
        return best_ext4, False
    raise MeasurementError(
        f"no ext4 or LUKS root partition found on {nbd} — is this a bootable image?"
    )


@contextlib.contextmanager
def _mounted_image_root(
    image: str, luks_passphrase: str | None, root_part: str | None = None
):
    """Mount the image's OS root read-only and yield the mount path.

    Uses qemu-nbd + (for an encrypted root) ``cryptsetup luksOpen`` + ``mount`` — the same tooling
    the build uses to CREATE the image — rather than libguestfs, whose appliance will not open a
    LUKS2/argon2id root here (it detects the header but the open silently fails). Requires root,
    which the measurement flow already has. Tears down mount -> luksClose -> nbd disconnect in all
    cases so a failure never leaks a mapping or an nbd connection.

    ``root_part`` forces the partition: an absolute ``/dev/...`` path, or a suffix (e.g. ``p1``)
    appended to the chosen nbd device. When unset the root is auto-detected.
    """
    if os.geteuid() != 0:
        raise MeasurementError(
            "RTMR3 needs root to mount the image (qemu-nbd/mount) — re-run with sudo"
        )
    # cryptsetup is required ONLY for an encrypted root (checked in the LUKS branch below), so a
    # plaintext (debug) image measures identically without it — the process after mounting is the
    # same for prod and debug; the only difference is the luksOpen step.
    for tool in ("qemu-nbd", "mount", "umount", "blkid"):
        if not _have(tool):
            raise MeasurementError(
                f"{tool} not found — install qemu-utils and util-linux"
            )

    proc.run(["modprobe", "nbd", "max_part=8"], capture_output=True, text=True)
    nbd = _free_nbd_device()
    mapper_name: str | None = None
    mnt: str | None = None
    connected = False
    try:
        result = proc.run(
            ["qemu-nbd", "--read-only", "--connect", nbd, image],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise MeasurementError(f"qemu-nbd connect failed: {result.stderr.strip()}")
        connected = True
        proc.run(["partprobe", nbd], capture_output=True, text=True)
        if not _wait_for_path(f"{nbd}p1"):
            raise MeasurementError(
                f"partitions did not appear on {nbd} after partprobe"
            )

        if root_part:
            part = root_part if root_part.startswith("/dev/") else f"{nbd}{root_part}"
            # blkid (not cryptsetup) tells LUKS from ext4, so plaintext images stay cryptsetup-free.
            fstype = proc.run(
                ["blkid", "-o", "value", "-s", "TYPE", part],
                capture_output=True,
                text=True,
            ).stdout.strip()
            is_luks = fstype == "crypto_LUKS"
        else:
            part, is_luks = _detect_root_partition(nbd)

        if is_luks:
            if not luks_passphrase:
                raise MeasurementError(
                    "image root is LUKS-encrypted — set LUKS_PASSPHRASE (the passphrase the image "
                    "was encrypted with) so RTMR3 can be recomputed from the unlocked root"
                )
            if not _have("cryptsetup"):
                raise MeasurementError(
                    "image root is LUKS-encrypted but cryptsetup is not installed — install it to "
                    "unlock the root (plaintext/debug images do not need cryptsetup)"
                )
            mapper_name = f"chutes-rtmr3-{os.getpid()}"
            # Passphrase via stdin (--key-file=-), never argv/ps; exact bytes, no trailing newline.
            result = proc.run(
                [
                    "cryptsetup",
                    "luksOpen",
                    "--readonly",
                    part,
                    mapper_name,
                    "--key-file=-",
                ],
                input=luks_passphrase,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise MeasurementError(
                    f"cryptsetup luksOpen failed: {result.stderr.strip()}"
                )
            source = f"/dev/mapper/{mapper_name}"
        else:
            source = part

        mnt = tempfile.mkdtemp(suffix="-rtmr3")
        result = proc.run(
            ["mount", "-o", "ro", source, mnt], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise MeasurementError(f"mount failed: {result.stderr.strip()}")
        yield mnt
    finally:
        if mnt:
            proc.run(["umount", mnt], capture_output=True, text=True)
            try:
                os.rmdir(mnt)
            except OSError:
                pass
        if mapper_name:
            proc.run(
                ["cryptsetup", "luksClose", mapper_name], capture_output=True, text=True
            )
        if connected:
            proc.run(["qemu-nbd", "--disconnect", nbd], capture_output=True, text=True)


def compute_rtmr3(
    image: str, root_part: str | None = None, luks_passphrase: str | None = None
) -> tuple[str, list[tuple[str, str]]]:
    """Compute RTMR3 by mounting the image's root read-only and replaying the file chain.

    Always recomputes fresh from the actual root — no cached/reused value. A plaintext ext4 root
    (the PRE-LUKS build stage) is mounted directly; an already-encrypted (post-LUKS) root is
    unlocked with ``luks_passphrase`` (the same passphrase the image was encrypted with). Both go
    through qemu-nbd + cryptsetup — the tooling that built the image — so it never depends on
    libguestfs. Requires root. ``root_part`` overrides the auto-detected root partition.

    Returns (uppercase hex, per-file [(sha384hex, root-relative path)]).
    """
    image = os.path.abspath(image)
    if not os.path.isfile(image):
        raise MeasurementError(f"image not found: {image}")
    with _mounted_image_root(image, luks_passphrase, root_part) as mnt:
        conf = os.path.join(mnt, "etc/tdx-measure.conf")
        if not os.path.isfile(conf):
            raise MeasurementError(
                "/etc/tdx-measure.conf not found in image — rtmr3-measure did not run"
            )
        # The guest's own helper does the hashing and the fold, so the value is the one the
        # guest extends at boot and rtmr3-verify recomputes, by construction.
        try:
            return rtmr3.compute_rtmr3(mnt, conf, tdx_measure_script())
        except rtmr3.Rtmr3Error as exc:
            raise MeasurementError(str(exc)) from exc
