#!/usr/bin/env python3
"""Offline per-topology RTMR0 generator → teeMeasurements block.

Implements the release-time generator from local/offline-rtmr0-findings.md §7:
RTMR0 is a SHA-384 chain over the CCEL's MrIndex==1 records — 14 events on this
branch's direct boot (19 on indirect, with the #15-18 boot variables) — of which
**5 vary** per topology and the rest are constant (firmware/boot). From one baseline
CCEL (the constants) plus a per-topology recompute of the varying events, splice +
replay → rtmr0. The varying events are located by identity (locate_rtmr0_events), so
the splice is correct regardless of that boot-method count.

The 5 varying events and how each is reproduced offline (no guest boot):

    #0   TdxTable (TD-HOB)      measure_td_hob(memory)      — per (mem) class
    #11  SHA384(etc/table-loader)   SHA384(fw_cfg blob)     — per topology
    #12  SHA384(etc/acpi/rsdp)      SHA384(fw_cfg blob)     — per topology
    #13  SHA384(etc/acpi/tables)    SHA384(fw_cfg blob)     — per topology
    #14  SMBIOS handoff         from baseline — host/topology-invariant (pinned identity)

#0 and #11-13 are recomputed per topology by the fork; #14 and the constants come from the
one baseline CCEL. SMBIOS's only host-varying input (the type-1/2/3 identity) is pinned this
release, so #14 does not vary by host or topology — one CCEL, captured anywhere, generates
every profile. (Recomputing #14 offline from the SMBIOS blob, to drop the CCEL entirely, is
future work — see utils/smbios_match.py.)

Verified end-to-end against local/acpi_real (box-028, RTX_PRO_6000) — see `selftest`.

Requires host-tools/scripts on sys.path (for chutes_cvm.guest / GPU_PROFILES) and, for
actual per-topology ACPI generation, the chutesai/tdx-measure fork + Docker on any
x86-64 Linux (NO TDX, NO GPUs — that's the point of offline measurement). The
splice/replay/recompute/assembly path is pure stdlib and runs anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from chutes_cvm.measurement import ccel_replay as cc
from chutes_cvm.paths import firmware_dir

# The topology-varying RTMR0 events are located BY IDENTITY (event type + descriptor),
# not by fixed position: the boot method sets how many CONSTANT events surround them
# (indirect boot = 19 events total; direct boot = 14 — no #15-18 boot variables and one
# fewer QEMU FW CFG), which shifts absolute positions. We recompute #0 (TD-HOB) and the
# three ACPI DATA digests; #14 (SMBIOS) and every constant stay from the baseline.
_EV_HANDOFF_TABLES2 = 0x8000000B  # TD-HOB (#0); data contains "TdxTable"
_EV_PLATFORM_CONFIG_FLAGS = (
    0x0000000A  # the three ACPI DATA events (#11-13) share this type
)

# The three ACPI DATA events, in fold order, → their fw_cfg blob name (as staged by
# extract-measurements.sh) for the #11-13 recompute.
ACPI_BLOB_FILES = ["table_loader.bin", "rsdp.bin", "acpi_tables.bin"]

# The fork's rtmr0_log (tdx-measure --json-file) indices for the events it recomputes:
# [0] = TD-HOB, [8,9,10] = the three ACPI DATA digests (table-loader / rsdp / acpi-tables).
FORK_TDHOB_IDX = 0
FORK_ACPI_IDX = (8, 9, 10)


def overrides_from_fork_log(
    rtmr0_log: list[str], tdhob_idx: int, acpi_idx: list[int]
) -> dict[int, bytes]:
    """Map the fork's recomputed digests onto the located baseline indices (from
    locate_rtmr0_events): fork #0 → TD-HOB, fork [8,9,10] → the three ACPI DATA events,
    in order. #14 (SMBIOS, same (mem,cpu) class) and #2/3/4 stay from the baseline."""
    need = max((FORK_TDHOB_IDX, *FORK_ACPI_IDX)) + 1
    if len(rtmr0_log) < need:
        raise ValueError(
            f"fork rtmr0_log has {len(rtmr0_log)} events, need >= {need}. "
            "Rebuild the tdx-measure fork with the rtmr0_log field, and confirm "
            "direct-boot mode (kernel/initrd set in the metadata)."
        )
    out = {tdhob_idx: bytes.fromhex(rtmr0_log[FORK_TDHOB_IDX])}
    for baseline_i, fork_i in zip(acpi_idx, FORK_ACPI_IDX):
        out[baseline_i] = bytes.fromhex(rtmr0_log[fork_i])
    return out


# ── Core: splice + replay ─────────────────────────────────────────────────────


def mr1_events(events: list[cc.Event]) -> list[cc.Event]:
    """The MrIndex==1 events that fold into RTMR0 (skipping EV_NO_ACTION), in order.
    Its length is boot-method-dependent (direct=14, indirect=19), so index the events
    the splice touches via locate_rtmr0_events, not by a fixed findings-§1 number."""
    return [e for e in events if e.mr_index == 1 and e.event_type != cc.EV_NO_ACTION]


def locate_rtmr0_events(events: list[cc.Event]) -> tuple[int, list[int]]:
    """Locate the spliced events in the MrIndex==1 fold BY IDENTITY, so it works for any
    boot method's event count. Returns (tdhob_index, [three acpi indices]): the TD-HOB
    (EV_EFI_HANDOFF_TABLES2 / "TdxTable") and the three ACPI DATA events
    (EV_PLATFORM_CONFIG_FLAGS / "ACPI DATA"), in fold order."""
    tdhob = None
    acpi: list[int] = []
    for i, e in enumerate(mr1_events(events)):
        if e.event_type == _EV_HANDOFF_TABLES2 and b"TdxTable" in e.data:
            tdhob = i
        elif e.event_type == _EV_PLATFORM_CONFIG_FLAGS and b"ACPI DATA" in e.data:
            acpi.append(i)
    if tdhob is None or len(acpi) != 3:
        raise cc.EventLogError(
            f"unexpected RTMR0 layout: TD-HOB found={tdhob is not None}, "
            f"{len(acpi)} ACPI DATA events (need exactly 1 + 3)."
        )
    return tdhob, acpi


def replay_with_overrides(
    events: list[cc.Event], overrides: dict[int, bytes], alg: int = cc.RTMR_ALG
) -> bytes:
    """Fold RTMR0 (MrIndex==1) substituting overrides[i] (i = index into mr1_events) for
    that event's SHA-384 digest. This is the splice: constant events keep their baseline
    digest, the located varying events get their recomputed one."""
    hash_name = cc._ALG_NAMES[alg]
    acc = b"\x00" * cc._ALG_SIZES[alg]
    for pos, ev in enumerate(mr1_events(events)):
        digest = overrides.get(pos)
        if digest is None:
            digest = ev.digest(alg)
            if digest is None:
                raise cc.EventLogError(f"event #{pos} ({ev.type_name}) missing digest")
        acc = hashlib.new(hash_name, acc + digest).digest()
    return acc


def acpi_digests(acpi_dir: str | Path, acpi_idx: list[int]) -> dict[int, bytes]:
    """{baseline_index: SHA384(blob)} for the three ACPI DATA events, mapping each located
    index to its fw_cfg blob (table-loader / rsdp / acpi-tables, in fold order). This is the
    #11-13 recompute, proven in findings §2: the ACPI DATA digests are literally SHA384 of
    the bytes."""
    acpi_dir = Path(acpi_dir)
    out: dict[int, bytes] = {}
    for baseline_i, fname in zip(acpi_idx, ACPI_BLOB_FILES):
        blob = acpi_dir / fname
        if not blob.exists():
            raise FileNotFoundError(f"missing fw_cfg blob {fname}: {blob}")
        out[baseline_i] = hashlib.sha384(blob.read_bytes()).digest()
    return out


# ── Per-topology ACPI generation (build host: tdx-measure + Docker) ────────────


def generate_acpi_blobs(
    metadata: dict, out_dir: Path, *, tdx_measure_bin: str, dist: str
) -> dict:
    """Run `tdx-measure --create-acpi-tables` to dump the topology's fw_cfg ACPI
    blobs and its {mrtd, rtmr0}. Mirrors local/scripts/run_validation.sh:43. The
    generated etc/acpi/* land next to `acpi_tables` in the metadata; we read those
    for the #11-13 recompute. Returns the parsed tdx-measure JSON ({mrtd, rtmr0}).

    Runs OFFLINE on any x86-64 Linux with Docker + the fork — no TDX, no GPUs. KVM
    speeds the brief ACPI-gen QEMU run but isn't required; reserve=off (applied by
    platform_tables.MeasurementMetadata) lifts the guest-sized-RAM requirement.

    Only the distribution is passed to --create-acpi-tables: the fork pins the exact
    QEMU source-package version *and* container image digest per dist (qemu_pkg_for),
    which is what makes the dump reproducible. Our internal baselined_measurements key
    (e.g. "10.2.1") is a release label, NOT a Debian package version — forwarding it as
    the fork's version override lands an unresolvable `pull-lp-source qemu 10.2.1`.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "metadata.json"
    result_path = out_dir / "result.json"
    meta_path.write_text(json.dumps(metadata, indent=2))
    # The metadata positional is placed first: --create-acpi-tables is num_args=1..=2,
    # so a metadata path immediately after `dist` would be greedily eaten as the version.
    # Capture the output (the fork's docker build log is very noisy) and, on failure,
    # raise just the tail — the caller renders it as a one-line PENDING reason.
    proc = subprocess.run(
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
    if proc.returncode != 0:
        tail = "\n    ".join(
            (proc.stderr or proc.stdout or "").strip().splitlines()[-4:]
        )
        raise RuntimeError(
            f"tdx-measure --create-acpi-tables (dist={dist}) failed "
            f"(exit {proc.returncode}):\n    {tail}"
        )
    return json.loads(result_path.read_text())


# ── Topology enumeration (offline, from the profile registry) ─────────────────


@dataclass(frozen=True)
class Topology:
    profile_name: str
    qemu_version: str
    fingerprint: object  # NumaTopology | FlatTopology

    def key(self) -> str:
        return f"{self.profile_name}[{self.qemu_version}]:{self.fingerprint}"


def enumerate_topologies(qemu_filter: str | None = None) -> list[Topology]:
    """Every registered (profile, qemu_version, fingerprint) from the profiles'
    `baselined_measurements` — the hand-curated offline registry (no live host).
    `qemu_filter` (e.g. "10.2.1") restricts to this release's supported QEMU."""
    from chutes_cvm.guest.gpu.profiles import GPU_PROFILES

    out: list[Topology] = []
    for name, profile in GPU_PROFILES.items():
        for qemu_version, fingerprints in profile.baselined_measurements.items():
            if qemu_filter and qemu_version != qemu_filter:
                continue
            for fp in fingerprints:
                out.append(Topology(name, qemu_version, fp))
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cmd_selftest(args: argparse.Namespace) -> int:
    """Prove the splice+recompute+replay path against the committed RTX fixture:
    recompute #11-13 from local/acpi_real's own ACPI blobs, splice them into that
    same baseline CCEL, and confirm the replay reproduces box-028's known RTMR0."""
    fixture = Path(args.fixture)
    ccel = fixture / "data" / "CCEL"
    if not ccel.exists():
        ccel = fixture / "CCEL"
    events = cc.parse_event_log(ccel.read_bytes())

    expected = cc.replay(events, 1).hex().upper()
    tdhob_idx, acpi_idx = locate_rtmr0_events(events)
    overrides = acpi_digests(fixture, acpi_idx)
    spliced = replay_with_overrides(events, overrides).hex().upper()

    # Cross-check: the recomputed ACPI digests must equal the captured event digests.
    captured = mr1_events(events)
    ok_acpi = all(overrides[i] == captured[i].digest() for i in acpi_idx)

    print(f"baseline replay   : {expected[:16]}…")
    print(f"spliced replay    : {spliced[:16]}…")
    print(f"#11-13 recompute  : {'MATCH captured' if ok_acpi else 'MISMATCH'}")

    # Also validate the fork-log → override path (what `generate` uses) without
    # needing tdx-measure: synthesize a fork rtmr0_log whose [0,8,9,10] entries are
    # the baseline's own TD-HOB/ACPI digests, run it through overrides_from_fork_log +
    # replay, and confirm it reproduces the baseline. Proves the index map + splice.
    synth = ["00" * 48] * (max((FORK_TDHOB_IDX, *FORK_ACPI_IDX)) + 1)
    synth[FORK_TDHOB_IDX] = captured[tdhob_idx].digest().hex()
    for baseline_i, fork_i in zip(acpi_idx, FORK_ACPI_IDX):
        synth[fork_i] = captured[baseline_i].digest().hex()
    forkpath = (
        replay_with_overrides(
            events, overrides_from_fork_log(synth, tdhob_idx, acpi_idx)
        )
        .hex()
        .upper()
    )
    ok_forklog = forkpath == expected
    print(f"fork-log override : {'MATCH baseline' if ok_forklog else 'MISMATCH'}")

    ok = spliced == expected and ok_acpi and ok_forklog
    if args.expect:
        ok = ok and spliced.startswith(args.expect.upper())
        print(
            f"expect {args.expect}: {'MATCH' if spliced.startswith(args.expect.upper()) else 'NO MATCH'}"
        )
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _cmd_generate(args: argparse.Namespace) -> int:
    """Generate per-topology RTMR0. --profile <name> does one profile; empty --profile
    does ALL. For each baselined topology, run tdx-measure to get its #0/#11-13
    (rtmr0_log), splice into the baseline CCEL (keeping its #14/#2-4/constants) and
    replay → RTMR0.

    The fork recomputes the per-topology events (#0 TD-HOB, #11-13 ACPI); the baseline
    supplies the constants and #14 (SMBIOS). #14's only host-varying input (the type-1/2/3
    identity) is pinned this release, so ONE CCEL — captured on any host, TDX or not —
    generates every profile offline; there is no per-class baseline requirement. The
    generated rtmr0 is validated against a live quote. Writes the profiles to --output.

    Needs the fork + Docker (offline, any x86-64 Linux — no TDX/GPU)."""
    from chutes_cvm.guest.gpu.profiles import GPU_PROFILES
    from chutes_cvm.measurement.platform_tables import MeasurementMetadata
    from chutes_cvm.measurement.topology_spec import (
        build_topology_spec,
        measurement_cpu_args,
    )

    def fork_rtmr0(profile, fp):
        """Run the fork to self-generate this topology's COMPLETE RTMR0 — all 15
        events, no CCEL, no splice. The measurement -cpu reconstructs the profile's
        production CPU vendor and the SMBIOS Type-4 Processor ID is patched in, so any
        host reproduces the production RTMR0. Returns (rtmr0, mrtd)."""
        spec = build_topology_spec(
            profile,
            fp,
            cpu_args=measurement_cpu_args(fp, args.qemu),
            firmware=str(Path(args.bios_dir) / profile.firmware_filename),
        )
        with tempfile.TemporaryDirectory() as td:
            meta = MeasurementMetadata(
                spec, profile, fp, acpi_tables=str(Path(td) / "acpi.bin")
            ).to_dict()
            out = generate_acpi_blobs(
                meta,
                Path(td),
                tdx_measure_bin=args.tdx_measure_bin,
                dist=args.dist,
            )
        return (out.get("rtmr0") or "").upper(), out.get("mrtd", "")

    names = [args.profile] if args.profile else list(GPU_PROFILES)
    hardware: list[dict] = []  # flat teeMeasurements `hardware` entries
    mrtds: set[str] = set()
    pending: list[str] = []
    for name in names:
        profile = GPU_PROFILES.get(name)
        if profile is None:
            print(f"unknown profile: {name}", file=sys.stderr)
            return 1
        fps = sorted(profile.baselined_measurements.get(args.qemu, set()), key=str)
        if not fps:
            continue  # nothing baselined for this QEMU version
        # A profile that can't be generated offline yet (e.g. no passthrough["gpu"]
        # modeled) must not take down the whole publish — mark it PENDING and continue.
        try:
            for fp in fps:
                rtmr0, mrtd = fork_rtmr0(profile, fp)
                mrtds.add(mrtd.upper())
                gpu_count = getattr(fp.gpu, "gpu_count", None) or len(
                    getattr(fp.gpu, "gpu_nodes", ())
                )
                hw_name = f"{profile.display_name} [{args.qemu}, {fp.variant_label}]"
                hardware.append(
                    {
                        "name": hw_name,
                        "description": (
                            f"{gpu_count}x {profile.expected_gpus[0].upper()} "
                            "GPU configuration"
                        ),
                        "rtmr0": rtmr0,
                        "expected_gpus": list(profile.expected_gpus),
                        "gpu_count": gpu_count,
                    }
                )
                print(
                    f"    {hw_name}  rtmr0={rtmr0[:16]}…",
                    file=sys.stderr,
                )
        except Exception as exc:
            pending.append(name)
            print(
                f"  {name}: PENDING — cannot generate offline: {exc}", file=sys.stderr
            )
            continue

    # Every hardware entry must have a globally-unique name — the computed
    # display_name + variant_label guarantee this today; assert it so a future
    # profile/topology collision fails the build loudly instead of silently merging.
    counts: dict[str, int] = {}
    for e in hardware:
        counts[e["name"]] = counts.get(e["name"], 0) + 1
    dupes = sorted(n for n, c in counts.items() if c > 1)
    if dupes:
        print(f"ERROR: duplicate hardware names: {dupes}", file=sys.stderr)
        return 1
    # MRTD is version-level (same OVMF/TDVF across every topology of a build).
    if len(mrtds) > 1:
        print(
            f"ERROR: MRTD differs across topologies: {sorted(mrtds)}", file=sys.stderr
        )
        return 1

    # Aggregate-ready block: version-level mrtd + a flat hardware list. rtmr1/rtmr2/
    # runtime_rtmr3 are pinned by the sibling roles and joined by aggregate-measurements.
    block: dict = {
        "version": args.version,
        "mrtd": next(iter(mrtds), ""),
        "hardware": hardware,
    }
    if pending:
        block["pending_profiles"] = sorted(set(pending))

    payload = json.dumps(block, indent=2) + "\n"
    if args.output == "-":
        sys.stdout.write(payload)
    else:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload)
        print(f"wrote {out}", file=sys.stderr)
    print(
        f"{len(hardware)} hardware entr{'y' if len(hardware) == 1 else 'ies'} generated"
        f"{f', pending: {block['pending_profiles']}' if pending else ''}",
        file=sys.stderr,
    )
    # Fail only when a specific profile was requested but couldn't be generated.
    return 2 if (args.profile and not hardware) else 0


def _cmd_list(args: argparse.Namespace) -> int:
    """List the supported topologies the generator would produce RTMR0 for."""
    for t in enumerate_topologies(args.qemu):
        print(t.key())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="chutes-cvm generate-measurements",
        description=__doc__.splitlines()[0],
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("selftest", help="validate splice/replay against a fixture")
    st.add_argument(
        "--fixture",
        required=True,
        help="dev cross-check fixture: a capture dir with data/CCEL + the fw_cfg ACPI blobs "
        "(table_loader.bin / rsdp.bin / acpi_tables.bin)",
    )
    st.add_argument("--expect", default="5FC09D10", help="expected RTMR0 hex prefix")
    st.set_defaults(func=_cmd_selftest)

    ls = sub.add_parser("list", help="list supported topologies")
    ls.add_argument("--qemu", default="10.2.1", help="QEMU version filter")
    ls.set_defaults(func=_cmd_list)

    gen = sub.add_parser(
        "generate",
        help="generate per-topology RTMR0 for a profile (build host: needs the tdx-measure fork + Docker/KVM)",
    )
    gen.add_argument(
        "--profile",
        default="",
        help="GPU profile (e.g. RTX_PRO_6000) — must match the baseline's class; "
        "empty = ALL profiles (each generated only if its class matches a baseline)",
    )
    gen.add_argument(
        "--baseline",
        default="",
        help="DEPRECATED (no longer used): the fork now self-generates the complete "
        "RTMR0, so no baseline CCEL is required. Retained as an accepted-but-ignored "
        "flag for callers mid-migration.",
    )
    gen.add_argument(
        "--version", required=True, help="image version (recorded in the output)"
    )
    gen.add_argument(
        "--output",
        required=True,
        help="output JSON, e.g. measurements/<ver>/rtmr0-<profile>.json",
    )
    gen.add_argument(
        "--qemu", default="10.2.1", help="QEMU version key in baselined_measurements"
    )
    gen.add_argument(
        "--tdx-measure-bin",
        default="tdx-measure",
        help="path to the tdx-measure fork binary",
    )
    gen.add_argument(
        "--dist", default="ubuntu:26.04", help="ACPI-dump container base image"
    )
    gen.add_argument(
        "--bios-dir",
        default=str(firmware_dir()),
        help="directory holding the OVMF firmware (profile.firmware_filename); the fork "
        "opens the metadata's 'bios' path, so it must resolve absolutely "
        "(default: chutes-cvm firmware dir; env CHUTES_CVM_FIRMWARE_DIR)",
    )
    gen.set_defaults(func=_cmd_generate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
