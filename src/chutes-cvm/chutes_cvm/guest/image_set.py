"""Published image-set manifest: the coherence contract for a direct-boot VM image.

A published image set is a directory holding the qcow2 and its direct-boot sidecars
plus a manifest that ties them together as one coherent unit::

    <dir>/<name>.qcow2   <dir>/<name>.vmlinuz   <dir>/<name>.initrd
    <dir>/<name>.cmdline <dir>/manifest.json

``manifest.json`` records artifacts by *role*, not filename, so the same manifest
verifies the set across the three places its files carry different names — the build
output (``<version>[-debug].*``), the R2 objects (``tdx-guest[-debug].*``), and the
local download (``tdx-guest[-debug].*`` inside a per-variant dir)::

    {
      "version": "1.4.0",
      "debug": false,
      "artifacts": {
        "qcow2":   {"sha256": "<hex>", "size": <int>},
        "vmlinuz": {"sha256": "<hex>", "size": <int>},
        "initrd":  {"sha256": "<hex>", "size": <int>},
        "cmdline": {"sha256": "<hex>", "size": <int>}
      },
      "firmware": {"OVMF.inteltdx.fd": "<sha256>", "OVMF.amdsev.fd": "<sha256>"}
    }

``firmware`` records the guest firmware the image was built and measured with. The firmware ships
with chutes-cvm rather than with the image, and the launch measurement covers its exact bytes, so
``verify`` refuses any other build. Sets from before it was recorded have no ``firmware`` and are
not checked.

The manifest is *the* integrity source — it replaces the hand-bumped expected-hash
constant, and it is the first thing that ties the boot artifacts to their qcow2
(previously the artifacts had no checksum at all, so a stale/mismatched set only surfaced
as an opaque boot or attestation failure). It is generated once over the finished
artifacts (``manifest``), published to R2 alongside the qcow2, and verified on the way in
(``verify``).

``chutes-cvm image download`` fetches the set + manifest and runs ``verify --full`` to check
every downloaded byte once. The launcher runs ``verify`` (size-only, cheap) to confirm
the on-disk set still matches — without re-hashing a multi-GB qcow2 on every boot.

Usage (``chutes-cvm image <verb>``; also ``python3 -m chutes_cvm.guest.image_set <verb>``)::

    # Fetch + verify a published base image set (production, or --debug).
    chutes-cvm image download [--debug]

    # Generate the manifest for a finished image (build / publish / capture staging).
    # Hashes <qcow2> and its <base>.{vmlinuz,initrd,cmdline} sidecars, writing
    # manifest.json into the qcow2's directory (that directory IS the set).
    chutes-cvm image manifest <qcow2> --version V [-o OUT] [--debug]

    # Verify an image-set directory and print QCOW2=/SHA256= for the caller to eval.
    chutes-cvm image verify [--full] <image-set-dir>

``verify`` prints shell assignments for the caller to ``eval``::

    QCOW2=<path-to-qcow2>
    SHA256=<qcow2 sha256 from the manifest>

and exits non-zero with a clear message if the set is missing, incomplete, or does not
match the manifest.
"""

import argparse
import glob
import hashlib
import json
import os
import shlex
import sys
from dataclasses import dataclass, field

from chutes_cvm import proc
from chutes_cvm.guest.tee import SnpTeeProvider, TdxTeeProvider
from chutes_cvm.paths import SCRIPTS_DIR, firmware_dir

# The guest firmware each platform boots, recorded in the manifest by filename.
FIRMWARE = (TdxTeeProvider.default_firmware, SnpTeeProvider.default_firmware)

# Roles in the manifest. The on-disk filename for each is the qcow2 basename with the
# role as its extension (<base>.qcow2 / <base>.vmlinuz / <base>.initrd / <base>.cmdline).
ROLES = ("qcow2", "vmlinuz", "initrd", "cmdline")

# Where `chutes-cvm image download` puts the production set; the launch default and the base a
# `host verify` checks when the config names no explicit `vm.base_image`.
DEFAULT_IMAGE_SET_DIR = "/var/lib/chutes/base-images/tdx-guest"

_CHUNK = 1024 * 1024


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_qcow2(image_dir: str) -> str:
    """Return the single ``*.qcow2`` in ``image_dir`` (error if zero or many)."""
    matches = sorted(glob.glob(os.path.join(image_dir, "*.qcow2")))
    if not matches:
        raise FileNotFoundError(f"no *.qcow2 in image-set directory: {image_dir}")
    if len(matches) > 1:
        raise ValueError(
            "multiple *.qcow2 in image-set directory "
            f"{image_dir}: {', '.join(os.path.basename(m) for m in matches)} "
            "— an image set holds exactly one image"
        )
    return matches[0]


def _role_paths(qcow2: str) -> dict[str, str]:
    """Map each role to its on-disk path, derived from the qcow2 by shared basename."""
    base = qcow2[: -len(".qcow2")]
    return {"qcow2": qcow2, **{r: f"{base}.{r}" for r in ROLES if r != "qcow2"}}


def write_manifest(qcow2: str, output: str, version: str, debug: bool = False) -> None:
    """Hash the qcow2 + its sidecars and chutes-cvm's guest firmware (the checkout's, in a
    build), and write the manifest to ``output``.

    Fails loudly if any of the four artifacts or either firmware is missing — a manifest must
    describe a complete set. This is the single generator used by the build, publish, and
    capture staging so the schema never drifts from what ``ImageSet.verify`` checks.
    """
    role_path = _role_paths(qcow2)
    firmware_path = {name: os.path.join(firmware_dir(), name) for name in FIRMWARE}
    missing = [
        f"{r} ({p})"
        for r, p in {**role_path, **firmware_path}.items()
        if not os.path.exists(p)
    ]
    if missing:
        raise FileNotFoundError(
            "cannot write manifest — image set is incomplete, missing: "
            + ", ".join(missing)
        )
    artifacts = {
        role: {"sha256": _sha256(path), "size": os.path.getsize(path)}
        for role, path in role_path.items()
    }
    with open(output, "w") as f:
        json.dump(
            {
                "version": version,
                "debug": debug,
                "artifacts": artifacts,
                "firmware": {n: _sha256(p) for n, p in firmware_path.items()},
            },
            f,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")


def _load_manifest(image_dir: str) -> dict:
    path = os.path.join(image_dir, "manifest.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"manifest.json missing in {image_dir} — the image set is incomplete; "
            "re-run `chutes-cvm image download`"
        )
    with open(path) as f:
        manifest = json.load(f)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or any(r not in artifacts for r in ROLES):
        raise ValueError(f"manifest.json in {image_dir} is missing artifact roles")
    return manifest


@dataclass(frozen=True)
class ImageArtifact:
    """One file of an image set: where it is, and what its manifest says it must be."""

    role: str
    path: str
    sha256: str
    size: int

    def problems(self, full: bool) -> list[str]:
        """How the file on disk differs from the manifest; ``full`` also re-hashes it."""
        if not os.path.exists(self.path):
            return [f"missing {self.role}: {self.path}"]
        actual_size = os.path.getsize(self.path)
        if actual_size != self.size:
            return [
                f"{self.role} size mismatch: {self.path} is {actual_size}, "
                f"manifest says {self.size}"
            ]
        if full:
            actual_sha = _sha256(self.path)
            if actual_sha != self.sha256:
                return [
                    f"{self.role} sha256 mismatch: {self.path}\n"
                    f"    manifest: {self.sha256}\n"
                    f"    actual:   {actual_sha}"
                ]
        return []


@dataclass(frozen=True)
class ImageSet:
    """A base image set on disk: one qcow2, its direct-boot sidecars, and the manifest tying them
    together as one build.

    ``version`` and ``rc`` say which build it is: ``rc`` is the manifest's ``debug`` flag, since a
    debug build attests under its ``rc:true`` measurement and a production build under its
    ``rc:false`` one. That pair is what a host class's published measurements are matched against.
    """

    directory: str
    version: str
    rc: bool
    qcow2: ImageArtifact
    vmlinuz: ImageArtifact
    initrd: ImageArtifact
    cmdline: ImageArtifact
    # Guest firmware filename -> sha256 the image was built with; empty for older sets.
    firmware: dict = field(default_factory=dict)

    @classmethod
    def from_dir(cls, directory: str) -> "ImageSet":
        """Read the set at ``directory``: its manifest and the files it names. Raises
        FileNotFoundError/ValueError for a missing qcow2 or manifest, or a manifest without a
        version. Does not check the files themselves; that is ``verify``."""
        qcow2 = _find_qcow2(directory)
        manifest = _load_manifest(directory)
        version = str(manifest.get("version") or "")
        if not version:
            raise ValueError(f"image set at {directory} has no version in its manifest")
        recorded = manifest["artifacts"]
        artifacts = {
            role: ImageArtifact(
                role=role,
                path=path,
                sha256=str(recorded[role].get("sha256", "")),
                size=recorded[role].get("size"),
            )
            for role, path in _role_paths(qcow2).items()
        }
        return cls(
            directory=directory,
            version=version,
            rc=bool(manifest.get("debug")),
            firmware=dict(manifest.get("firmware") or {}),
            **artifacts,
        )

    @property
    def label(self) -> str:
        return f"{self.version}{' (rc)' if self.rc else ''}"

    def verify(self, full: bool, firmware: str = "") -> None:
        """Check the files against the manifest; raise ValueError listing every mismatch.

        ``full`` re-hashes every file (download-time). Otherwise only presence and size are
        checked (launch-time) -- the bytes were already verified when downloaded. The guest
        firmware in ``firmware`` (by default chutes-cvm's own) is always hashed: it ships apart
        from the image, so it was never verified with it.
        """
        problems = [
            problem
            for artifact in (self.qcow2, self.vmlinuz, self.initrd, self.cmdline)
            for problem in artifact.problems(full)
        ]
        for name, expected in self.firmware.items():
            path = os.path.join(firmware or str(firmware_dir()), name)
            actual = _sha256(path) if os.path.isfile(path) else "missing"
            if actual != expected:
                problems.append(
                    f"guest firmware {path} is {actual}, but the image was built with "
                    f"{expected}; install the chutes-cvm that matches this image"
                )
        if problems:
            raise ValueError(
                "image set does not match its manifest — the qcow2 and its boot artifacts "
                "are out of sync:\n  " + "\n  ".join(problems)
            )


def _cmd_download(args: argparse.Namespace) -> int:
    """Fetch + manifest-verify a published base image set (production, or debug with --debug).

    Delegates to the bundled download-image-set.sh, which downloads the full set into
    /var/lib/chutes/base-images/<variant>/ and runs `image verify --full` over it.
    """
    base = "tdx-guest-debug" if args.debug else "tdx-guest"
    script = SCRIPTS_DIR / "download-image-set.sh"
    if not script.exists():
        print(
            f"chutes-cvm: download-image-set.sh not found at {script}", file=sys.stderr
        )
        return 1
    return proc.call(["bash", str(script), base])


def _cmd_verify(args: argparse.Namespace) -> int:
    try:
        image = ImageSet.from_dir(args.image_dir)
        image.verify(args.full)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"QCOW2={shlex.quote(image.qcow2.path)}")
    print(f"SHA256={image.qcow2.sha256}")
    return 0


def _cmd_manifest(args: argparse.Namespace) -> int:
    if not args.qcow2.endswith(".qcow2"):
        print(f"ERROR: expected a .qcow2 path, got {args.qcow2}", file=sys.stderr)
        return 1
    # Default: manifest.json beside the qcow2 — the image set lives in its own directory,
    # and manifest.json is the only name _load_manifest (verify / launch / download) looks
    # for, so the generated set is directly consumable and copyable as-is.
    output = args.output or os.path.join(os.path.dirname(args.qcow2), "manifest.json")
    try:
        write_manifest(args.qcow2, output, version=args.version, debug=args.debug)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chutes-cvm image")
    sub = parser.add_subparsers(dest="command", required=True)

    p_download = sub.add_parser(
        "download",
        help="download + verify a published base image set (production; --debug for the debug set)",
    )
    p_download.add_argument(
        "--debug",
        action="store_true",
        help="fetch the debug set (SSH enabled, no encryption) instead of production",
    )
    p_download.set_defaults(func=_cmd_download)

    p_verify = sub.add_parser(
        "verify", help="verify an image-set directory and print QCOW2=/SHA256="
    )
    p_verify.add_argument("image_dir", help="path to the image-set directory")
    p_verify.add_argument(
        "--full",
        action="store_true",
        help="re-hash every file (download-time); default checks presence and size only",
    )
    p_verify.set_defaults(func=_cmd_verify)

    p_manifest = sub.add_parser(
        "manifest", help="generate manifest.json for a finished image + its sidecars"
    )
    p_manifest.add_argument("qcow2", help="path to the finished .qcow2")
    p_manifest.add_argument(
        "-o",
        "--output",
        default="",
        help="manifest path (default: manifest.json next to the qcow2)",
    )
    p_manifest.add_argument("--version", required=True, help="image version")
    p_manifest.add_argument(
        "--debug", action="store_true", help="mark the set as a debug build (metadata)"
    )
    p_manifest.set_defaults(func=_cmd_manifest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
