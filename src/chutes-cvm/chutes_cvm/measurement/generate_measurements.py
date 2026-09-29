#!/usr/bin/env python3
"""TEE measurements generator: one release's teeMeasurements entry, both platforms.

`generate` reads the known host classes from the API (`GET /servers/tdx/host_profiles`, the
source of truth), measures each on its own platform, and writes the version's measurements.yaml:

    tdx:  mrtd, rtmr1, rtmr2, rtmr3, hardware[].rtmr0      (Intel classes; tdx.TdxMeasurements)
    snp:  hardware[].measurement                           (AMD classes; snp.SnpMeasurements)

One guest image boots on both platforms, so every release emits both sections. The API's
fingerprint is carried onto each entry so the reconciler can join a published measurement to the
submitted host profile.

By default only *measured* host classes are processed: the set a third party can verify against
a published measurement. `--include-pending` also processes classes awaiting their first
measurement, which is what the release build passes to turn new submissions into published
measurements. A measured class that fails to generate fails the run; a pending one stays pending.

`--register rtmr3` computes RTMR3 alone (the standalone partial for the GPU-VM build, which has
no aggregation). `list` prints the API's known host classes.

This module is only the run: fetching classes, dispatching them, and writing the file. What each
platform measures lives in tdx.py and snp.py; what they share, in platform.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.measurement.platform import MeasurementError, PlatformMeasurements
from chutes_cvm.measurement.snp import SnpMeasurements
from chutes_cvm.measurement.tdx import TdxMeasurements, compute_rtmr3
from chutes_cvm.paths import DEFAULT_API_BASE, firmware_dir

# The API is the source of truth for known host classes and their fingerprints. `generate`
# reads the published host profiles (the platform inputs each measurement is built from) from
# this public, unauthenticated endpoint and generates one measurement per profile, carrying the
# API's fingerprint straight through — the reconciler joins published measurements to submitted
# host profiles on it, so an entry without a fingerprint is unmatchable.
_HOST_PROFILES_PATH = "/servers/tdx/host_profiles"


# ── host classes from the API ──────────────────────────────────────────────────────────────────


def fetch_host_profiles(api_base: str, include_pending: bool = False) -> list[dict]:
    """GET the published host profiles: ``[{"fingerprint", "measured", "profile"}, ...]``.

    The API owns the fingerprint and is the source of truth for known host classes. This public,
    unauthenticated endpoint returns each stored discover-profile document plus its 64-hex
    fingerprint; the generator builds one measurement per profile and carries the fingerprint
    through verbatim (never recomputed).

    Default is measured-only — the host classes a third party can verify against a published
    measurement. ``include_pending`` also returns classes awaiting generation (the generator's
    queue: a submitted profile is only "measured" once its measurement exists, so generation must
    fetch the pending set first)."""
    url = f"{api_base.rstrip('/')}{_HOST_PROFILES_PATH}"
    if include_pending:
        url += "?include_pending=true"
    req = urllib.request.Request(
        url, headers={"User-Agent": "chutes-cvm-measurements/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise ValueError(f"API returned HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"API unreachable at {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"API returned unparseable host profiles: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(
            f"expected a list of host profiles from {url}, got {type(data).__name__}"
        )
    return data


def resolve_hardware_names(hardware: list[dict]) -> None:
    """Validate hardware-entry identity and disambiguate colliding labels, in place.

    A host class is identified by its FINGERPRINT, not its name. Whenever the fingerprint's
    inputs change, every class re-registers under a new fingerprint while the old record stays
    live -- hosts upgrade at different times, so both must remain until the fleet has moved.
    Those entries resolve to the same display_name + variant_label by construction, so one
    topology legitimately appears under several fingerprints.

    This used to assert name uniqueness, which turned that expected churn into a hard build
    failure and stalled releases behind any fingerprint schema change.

    Nothing about the NAME is an invariant, so nothing about it is enforced. It is built from
    display_name + qemu + variant_label, a strict subset of what feeds rtmr0 (per-profile
    cpu_vendor / phys_bits / cpu_processor_id all move rtmr0 and appear in none of them), so
    entries may share a name while differing in rtmr0 without anything being wrong -- the label
    is simply coarser than the measurement. It is safe to leave unconstrained because the API
    never keys on it: quotes match by MRTD + RTMRs (configs may share an RTMR0) and name reaches
    the API only as a log label. Colliding labels are suffixed so logs stay readable.

    Raises ValueError only on a duplicate fingerprint -- the key repeated, i.e. corrupt input.
    """
    fp_counts: dict[str, int] = {}
    for e in hardware:
        fp_counts[e["fingerprint"]] = fp_counts.get(e["fingerprint"], 0) + 1
    dupe_fps = sorted(f for f, c in fp_counts.items() if c > 1)
    if dupe_fps:
        raise ValueError(f"duplicate host-profile fingerprints: {dupe_fps}")

    by_name: dict[str, list[dict]] = {}
    for e in hardware:
        by_name.setdefault(e["name"], []).append(e)

    for name, entries in by_name.items():
        if len(entries) > 1:
            for e in entries:
                e["name"] = f"{name} ({e['fingerprint'][:12]})"


# ── dispatch: each host class to its own platform ───────────────────────────────────────────


def measure_host_classes(
    records: list[dict], platforms: list[PlatformMeasurements]
) -> list[str]:
    """Measure each API host class on its own platform; return those still PENDING.

    A class is Intel or AMD, never both, and its CPU vendor says which
    (``HostProfile.tee_provider``), so each lands in exactly one platform.

    A class that cannot be generated is judged by the API's ``measured`` flag:

    * **never measured** (``measured: false``) -- it stays PENDING: still in the generator's
      queue, reported but not blocking the release. E.g. an uncaptured CPU (``processor_id``
      null).
    * **measured before** (``measured: true``) -- a regression. Hosts of that class attest today,
      and publishing a release without it would leave them nothing to attest against, so every
      such class is collected and the run fails with all of them.

    Release inputs never reach this: the platforms loaded them before it runs.
    """
    by_provider = {p.provider: p for p in platforms}
    pending: list[str] = []
    regressions: list[str] = []
    for record in records:
        fingerprint = record.get("fingerprint") or ""
        label = fingerprint[:12] or "<no-fingerprint>"
        try:
            if not fingerprint:
                raise ValueError("host profile has no fingerprint")
            host = HostProfile.from_api_profile(record.get("profile") or {})
            provider = type(host.tee_provider)
            if provider not in by_provider:
                raise ValueError(
                    f"no measurements are generated for {provider.__name__}"
                )
            by_provider[provider].add(host, fingerprint)
        except Exception as exc:
            if record.get("measured"):
                regressions.append(f"{label}: {exc}")
                print(
                    f"  {label}: FAILED (previously measured): {exc}", file=sys.stderr
                )
            else:
                pending.append(fingerprint or label)
                print(f"  {label}: PENDING — not generated yet: {exc}", file=sys.stderr)

    if regressions:
        raise ValueError(
            f"{len(regressions)} previously measured host class(es) failed to generate; a "
            "release without them would leave those hosts unable to attest:\n    "
            + "\n    ".join(regressions)
        )
    # Across every platform: the lists land in one measurements.yaml, so a name colliding
    # between them still needs disambiguating, and a fingerprint repeated across them is the
    # same corrupt input it would be within one. Entries are mutated in place.
    resolve_hardware_names([e for p in platforms for e in p.hardware])
    return sorted(set(pending))


# ── the run and the CLI ─────────────────────────────────────────────────────────────────────────


def _write_output(payload: str, output: str) -> None:
    """Write ``payload`` to ``output`` — ``-`` = stdout; otherwise mkdir -p the parent, write
    the file, and note it on stderr."""
    if output == "-":
        sys.stdout.write(payload)
        return
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload)
    print(f"wrote {out}", file=sys.stderr)


def _compute_measurements(args: argparse.Namespace) -> dict:
    """Compute a release's whole teeMeasurements entry: both platforms, every host class.

    One guest image boots on both platforms, so every release measures both, whatever classes the
    API knows today; a platform's section is written only when it has classes, since the API
    refuses a section with no hardware (and with it the whole config). Each platform loads what
    its classes share first (TDX: RTMR1-3
    from the image; SEV-SNP: firmware and direct-boot artifacts), then each API host class is
    measured on its own platform. Pure data assembly -- no file output; raises ValueError
    (topology/aggregation) or MeasurementError (a missing or unreadable input).
    """
    platforms: list[PlatformMeasurements] = [
        TdxMeasurements(
            bios_dir=args.bios_dir,
            tdx_measure_bin=args.tdx_measure_bin,
            dist=args.dist,
            image=args.image,
        ),
        SnpMeasurements(bios_dir=args.bios_dir, image=args.image),
    ]
    records = fetch_host_profiles(args.api_base, args.include_pending)
    pending = measure_host_classes(records, platforms)
    if pending:
        print(f"    pending profiles: {pending}", file=sys.stderr)
    if not any(p.hardware for p in platforms):
        raise ValueError(
            "no measurements generated — the API returned no host profiles that could be "
            f"generated offline{f' (pending: {pending})' if pending else ''}"
        )
    # Insertion order matches the chutes-ops values.yaml teeMeasurements layout this merges
    # into; sort_keys=False keeps it.
    return {
        "version": args.version,
        **{p.key: p.section() for p in platforms if p.hardware},
    }


def _generate_full(args: argparse.Namespace) -> int:
    """A full `generate` (no --register): compute every register (via _compute_measurements) and
    write the version's single measurements.yaml to --output. compute → serialize → write.
    """
    try:
        entry = _compute_measurements(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except MeasurementError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = yaml.safe_dump(
        {"measurements": [entry]}, sort_keys=False, indent=2, default_flow_style=False
    )
    _write_output(payload, args.output)
    counts = ", ".join(
        f"{len(entry[tee]['hardware']) if tee in entry else 0} {tee}"
        for tee in ("tdx", "snp")
    )
    print(f"measurements.yaml: {counts} hardware entries", file=sys.stderr)
    return 0


def _generate_rtmr3(args: argparse.Namespace) -> int:
    """`generate --register rtmr3`: compute the version-level RTMR3 fresh from the image. Prints
    the bare hex to stdout (per-file hashes to stderr) for a caller to capture as a fact.

    Mounts the root read-only. If it is already LUKS-encrypted, set the LUKS_PASSPHRASE env var
    (the passphrase the image was encrypted with) to unlock it and recompute — always a fresh
    value, never a cached one.
    """
    try:
        rtmr3, per_file = compute_rtmr3(
            args.image,
            root_part=args.root_part,
            luks_passphrase=os.environ.get("LUKS_PASSPHRASE"),
        )
    except MeasurementError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"  Measuring {len(per_file)} files:", file=sys.stderr)
    for file_hash, rel in per_file:
        print(f"  {file_hash}  {rel}", file=sys.stderr)
    print(f"RTMR3: {rtmr3}", file=sys.stderr)
    print(rtmr3)  # bare hex to stdout
    return 0


def _usage_error(msg: str) -> int:
    """Print an argparse-style usage error to stderr and return exit code 2."""
    print(f"chutes-cvm measurements generate: {msg}", file=sys.stderr)
    return 2


def _cmd_generate(args: argparse.Namespace) -> int:
    """Route `measurements generate` by --register: none = the full measurements.yaml (both
    platforms, every register); rtmr3 = just the bare RTMR3 hex. Each mode needs different
    inputs, validated here (argparse can't require them conditionally).
    """
    if args.register == "rtmr3":
        if not args.image:
            return _usage_error("--register rtmr3 requires --image")
        return _generate_rtmr3(args)
    missing = [
        flag
        for flag, val in (("--version", args.version), ("--image", args.image))
        if not val
    ]
    if missing:
        return _usage_error(f"a full generate requires {' and '.join(missing)}")
    return _generate_full(args)


def _cmd_list(args: argparse.Namespace) -> int:
    """List the published host classes the API knows — the profiles `generate` builds
    measurements for. Prints ``<fingerprint> [pending] <count>x [<device_ids>]`` per class.
    """
    for record in fetch_host_profiles(args.api_base, args.include_pending):
        fp = record.get("fingerprint") or "<no-fingerprint>"
        state = "" if record.get("measured", True) else " [pending]"
        gpus = (record.get("profile") or {}).get("gpus") or []
        ids = ",".join(sorted({g.get("device_id") or "?" for g in gpus})) or "?"
        print(f"{fp}{state}  {len(gpus)}x [{ids}]")
    return 0


def _add_api_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--api-base",
        default=os.environ.get("CHUTES_API_BASE") or DEFAULT_API_BASE,
        help="control-plane base URL for the host-profile source "
        f"(default: {DEFAULT_API_BASE}; env CHUTES_API_BASE)",
    )
    p.add_argument(
        "--include-pending",
        action="store_true",
        help="also process host classes awaiting measurement generation (the generator's "
        "queue — use after a new host profile is submitted); default is measured classes only, "
        "which is what third parties verify against published measurements",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="chutes-cvm measurements",
        description=__doc__.splitlines()[0],
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser(
        "list", help="list the API's known host classes (fingerprint + GPUs)"
    )
    _add_api_arg(ls)
    ls.set_defaults(func=_cmd_list)

    def _add_fork_args(p: argparse.ArgumentParser) -> None:
        """Shared RTMR0-generation options (the tdx-measure fork inputs + host-profile source)."""
        _add_api_arg(p)
        p.add_argument(
            "--tdx-measure-bin",
            default="tdx-measure",
            help="path to the tdx-measure fork binary",
        )
        p.add_argument(
            "--dist", default="ubuntu:26.04", help="ACPI-dump container base image"
        )
        p.add_argument(
            "--bios-dir",
            default=str(firmware_dir()),
            help="directory holding both guest firmware images: OVMF.inteltdx.fd (TDX) and "
            "OVMF.amdsev.fd (SEV-SNP). Both measurements hash these exact bytes, and the "
            "fork opens the TDX one by absolute path "
            "(default: chutes-cvm firmware dir; env CHUTES_CVM_FIRMWARE_DIR)",
        )

    gen = sub.add_parser(
        "generate",
        help="generate the version's measurements — by default EVERY register into "
        "measurements.yaml; --register narrows it to one (build host: fork + Docker/KVM; "
        "LUKS_PASSPHRASE unlocks an encrypted root for RTMR3)",
        description="Generate TEE measurements for an image version. With no --register this "
        "computes the complete set from the finalized (post-LUKS) image — TDX: mrtd + rtmr0 (per "
        "Intel class) + rtmr1/rtmr2 + rtmr3; SEV-SNP: the launch digest (per AMD class) — and "
        "writes measurements.yaml. --register rtmr3 emits only the bare RTMR3 hex to stdout.",
    )
    _add_fork_args(gen)
    gen.add_argument(
        "--register",
        choices=("rtmr3",),
        default=None,
        help="generate only this register instead of the full set (rtmr3 = bare hex). "
        "Omit for the complete measurements.yaml.",
    )
    gen.add_argument(
        "--version",
        default=None,
        help="image version (required for the full set)",
    )
    gen.add_argument(
        "--image",
        default=None,
        help="finalized (post-luks) qcow2 (required for the full set and --register rtmr3): its "
        "staged .vmlinuz/.initrd/.cmdline pin RTMR1/RTMR2; its root (unlocked via LUKS_PASSPHRASE "
        "if encrypted) yields RTMR3",
    )
    gen.add_argument(
        "--output",
        default="-",
        help="output path for the full measurements.yaml; '-' = stdout (default). "
        "--register rtmr3 always prints its hex to stdout.",
    )
    gen.add_argument(
        "--root-part",
        default=None,
        help="root partition override for --register rtmr3: an absolute /dev path or an nbd "
        "suffix like 'p1' (default: auto-detect the LUKS/ext4 root on the nbd device)",
    )
    gen.set_defaults(func=_cmd_generate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
