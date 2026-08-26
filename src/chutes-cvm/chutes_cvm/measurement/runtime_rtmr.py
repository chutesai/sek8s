"""Version-level runtime RTMRs (RTMR1/RTMR2/RTMR3), computed offline at build time.

Unlike RTMR0 (firmware + per-topology ACPI; see generate_measurements.py), these are
**version-level** — identical across GPU topologies — and derive from the built image:

    RTMR1/RTMR2  the direct-boot kernel/initrd/cmdline (via the tdx-measure fork,
                 --runtime-only). Post-LUKS: LUKS rebuilds the initrd, and RTMR2 measures
                 that final one.
    RTMR3        the SHA-384 extension chain over the userspace files named in the image's
                 /etc/tdx-measure.conf. Content-derived — normally computed PRE-LUKS against the
                 plaintext root; a re-run against an already-encrypted image unlocks it with the
                 LUKS passphrase and recomputes fresh (never a cached value).

Both replay exactly what the launcher boots / the guest measures, so the pinned values match
the running VM by construction. Ports host-tools' former compute-rtmr1-2.sh / compute-rtmr3.sh.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from chutes_cvm import proc


class MeasurementError(RuntimeError):
    """A runtime-RTMR computation failed (missing input, tool error, parse failure)."""


def _have(tool: str) -> bool:
    """True if ``tool`` is on PATH."""
    return shutil.which(tool) is not None


# ── RTMR1 / RTMR2 (direct boot; version-level) ─────────────────────────────────

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
    base = os.path.splitext(image)[0]
    kernel, initrd = base + ".vmlinuz", base + ".initrd"
    cmdline_file = base + ".cmdline"
    for f in (kernel, initrd, cmdline_file):
        if not os.path.isfile(f):
            raise MeasurementError(
                f"missing direct-boot artifact {f} — run stage-boot-artifacts first"
            )
    # $(cat file) semantics: drop trailing newlines from the staged cmdline.
    cmdline = Path(cmdline_file).read_text().rstrip("\n")

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


# ── RTMR3 (userspace file chain; version-level, LUKS-independent) ──────────────


def _detect_ext4_root(image: str, key_args: "list[str] | tuple" = ()) -> str:
    """Return the first ext4 filesystem device in the image (guestfish list-filesystems).

    ``key_args`` (``--key all:file:<keyfile>``) unlock a LUKS root so the decrypted ext4 shows.
    """
    result = proc.run(
        ["guestfish", "--ro", "-a", image, *key_args],
        input="run\nlist-filesystems\n",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MeasurementError(
            f"guestfish failed to list filesystems: {result.stderr.strip()}"
        )
    for line in result.stdout.splitlines():
        # lines look like "/dev/sda2: ext4"
        dev, _, fstype = line.partition(":")
        if fstype.strip() == "ext4":
            return dev.strip()
    raise MeasurementError(
        "could not find an ext4 root partition; pass root_part explicitly"
    )


def _measured_files(mount_root: str, conf_path: str) -> list[tuple[str, str]]:
    """(root-relative path, full mounted path) for every regular non-symlink file named by
    /etc/tdx-measure.conf, sorted by root-relative path — matching rtmr3-measure/-verify.
    """
    cfg_paths: list[str] = []
    for line in Path(conf_path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            cfg_paths.append(line)
    if not cfg_paths:
        raise MeasurementError("no paths configured in tdx-measure.conf")

    root = mount_root.rstrip("/")
    rootlen = len(root)
    entries: list[tuple[str, str]] = []
    for cfg_path in cfg_paths:
        mounted = Path(root + cfg_path)
        if mounted.is_dir():
            for f in mounted.rglob("*"):
                if f.is_file() and not f.is_symlink():
                    entries.append((str(f)[rootlen:], str(f)))
        elif mounted.is_file() and not mounted.is_symlink():
            entries.append((cfg_path, str(mounted)))
    entries.sort(key=lambda e: e[0])
    if not entries:
        raise MeasurementError(
            "no files found to measure — check tdx-measure.conf paths"
        )
    return entries


def rtmr3_chain(files: list[tuple[str, str]]) -> tuple[str, list[tuple[str, str]]]:
    """Replay the RTMR3 extension chain over ``files`` (root-relative path, full path).

    ``rtmr3 = 0x00*48; for f: rtmr3 = SHA384(rtmr3 || SHA384(f.contents))`` — identical to
    rtmr3-measure (initramfs) and rtmr3-verify. Returns (uppercase hex, [(per-file-hash,
    root-relative path)]) — the pure, host-independent core, unit-testable without an image.
    """
    rtmr3 = bytes(48)
    per_file: list[tuple[str, str]] = []
    for rel_path, full_path in files:
        file_hash = hashlib.sha384(Path(full_path).read_bytes()).digest()
        rtmr3 = hashlib.sha384(rtmr3 + file_hash).digest()
        per_file.append((file_hash.hex(), rel_path))
    return rtmr3.hex().upper(), per_file


def root_is_luks(image: str) -> bool:
    """True if the image's root is LUKS-encrypted (virt-filesystems prints ``crypto_LUKS``)."""
    result = proc.run(
        ["virt-filesystems", "--long", "--all", "-a", image],
        capture_output=True,
        text=True,
    )
    return "crypto_LUKS" in result.stdout


def compute_rtmr3(
    image: str, root_part: str | None = None, luks_passphrase: str | None = None
) -> tuple[str, list[tuple[str, str]]]:
    """Compute RTMR3 by mounting the image read-only and replaying the file chain.

    Always recomputes fresh from the actual root — no cached/reused value. If the root is a
    plaintext ext4 (the normal PRE-LUKS build stage), it is mounted directly. If it is already
    LUKS-encrypted (a re-run against a finalized image), ``luks_passphrase`` (the same passphrase
    the image was encrypted with) unlocks it; without it, this errors rather than guessing.

    Returns (uppercase hex, per-file [(sha384hex, root-relative path)]). Requires guestmount
    (libguestfs-tools). ``root_part`` overrides the ext4 auto-detection.
    """
    if not os.path.isfile(image):
        raise MeasurementError(f"image not found: {image}")
    if not _have("guestmount") or not _have("guestfish"):
        raise MeasurementError(
            "guestmount/guestfish not found — install libguestfs-tools "
            "(sudo apt install libguestfs-tools)"
        )

    key_args: list[str] = []
    keyfile: str | None = None
    if root_is_luks(image):
        if not luks_passphrase:
            raise MeasurementError(
                "image root is LUKS-encrypted — set LUKS_PASSPHRASE (the passphrase the image "
                "was encrypted with) so RTMR3 can be recomputed from the unlocked root"
            )
        # Pass the key via a mode-600 temp file (all:file:) so it never lands in argv/ps.
        fd, keyfile = tempfile.mkstemp(suffix="-luks-key")
        os.write(fd, luks_passphrase.encode())
        os.close(fd)
        key_args = ["--key", f"all:file:{keyfile}"]

    try:
        part = root_part or _detect_ext4_root(image, key_args)
        mnt = tempfile.mkdtemp(suffix="-rtmr3")
        try:
            result = proc.run(
                ["guestmount", "--ro", "-a", image, *key_args, "-m", part, mnt],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise MeasurementError(f"guestmount failed: {result.stderr.strip()}")
            try:
                conf = os.path.join(mnt, "etc/tdx-measure.conf")
                if not os.path.isfile(conf):
                    raise MeasurementError(
                        "/etc/tdx-measure.conf not found in image — rtmr3-measure did not run"
                    )
                return rtmr3_chain(_measured_files(mnt, conf))
            finally:
                proc.run(["guestunmount", mnt], capture_output=True)
        finally:
            try:
                os.rmdir(mnt)
            except OSError:
                pass
    finally:
        if keyfile:
            try:
                os.remove(keyfile)
            except OSError:
                pass
