#!/usr/bin/env python3
"""Offline per-topology RTMR0 generator → teeMeasurements block.

Implements the release-time generator from local/offline-rtmr0-findings.md §7:
RTMR0 is a 19-event SHA-384 chain over the CCEL's MrIndex==1 records, of which
**14 are constant** (firmware/boot, topology-independent) and **5 vary** per
topology. From one baseline CCEL (the 14 constants) plus a per-topology recompute
of the varying events, splice + replay → rtmr0 for every supported topology.

The 5 varying events and how each is reproduced offline (no guest boot):

    #0   TdxTable (TD-HOB)      measure_td_hob(memory)      — per (mem) class
    #11  SHA384(etc/table-loader)   SHA384(fw_cfg blob)     — per topology
    #12  SHA384(etc/acpi/rsdp)      SHA384(fw_cfg blob)     — per topology
    #13  SHA384(etc/acpi/tables)    SHA384(fw_cfg blob)     — per topology
    #14  SMBIOS handoff         per (mem, cpu-count) class  — from baseline / gap

Within one profile (fixed mem + cpu), only #11-13 change (NUMA/flat layout), so
#0/#14 are reused from that profile's baseline and only the three ACPI digests are
recomputed. Across profiles, #0 also changes (measure_td_hob) and #14 is the open
SMBIOS-preimage gap (see smbios_match.py) — resolve per (mem, cpu) class.

Verified end-to-end against local/acpi_real (box-028, RTX_PRO_6000) — see `selftest`.

Requires host-tools/scripts on sys.path (for chutes.guest / GPU_PROFILES) and, for
actual per-topology ACPI generation, the chutesai/tdx-measure fork + Docker on any
x86-64 Linux (NO TDX, NO GPUs — that's the point of offline measurement). The
splice/replay/recompute/assembly path is pure stdlib and runs anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent / "host-tools" / "scripts"))

import ccel_replay as cc  # noqa: E402

# Positions (index within the MrIndex==1 fold, matching findings §1) of the varying
# events. #11-13 are the ACPI DATA digests; #0 the TD-HOB; #14 the SMBIOS handoff.
ACPI_EVENT_POS = {11: "etc/table-loader", 12: "etc/acpi/rsdp", 13: "etc/acpi/tables"}
TDHOB_POS = 0
SMBIOS_POS = 14

# The fw_cfg blob filenames (as staged by extract-measurements.sh) for #11-13.
ACPI_BLOB_FILES = {
    "etc/table-loader": "table_loader.bin",
    "etc/acpi/rsdp": "rsdp.bin",
    "etc/acpi/tables": "acpi_tables.bin",
}

# Map from the FORK's rtmr0_log index (tdx-measure --json-file, direct-boot order)
# to the canonical baseline event position we override. The fork's direct-boot log is:
#   [0]#0 td_hob [1]#1 cfv [2-6]#5-9 secureboot [7]#10 sep
#   [8]#11 table-loader [9]#12 rsdp [10]#13 acpi-tables [11]#15 BootOrder [12]#16
# The fork omits #2/3/4 (QEMU fw_cfg) and #14 (SMBIOS); those stay from the baseline.
FORK_LOG_TO_CANONICAL = {0: 0, 8: 11, 9: 12, 10: 13}


def overrides_from_fork_log(rtmr0_log: list[str]) -> dict[int, bytes]:
    """Turn the fork's per-event rtmr0_log (hex strings) into {canonical_pos: digest}
    for the topology-varying events the fork computes — #0 (TD-HOB) and #11-13 (ACPI).
    #14 (SMBIOS, same (mem,cpu) class) and #2/3/4 stay from the baseline."""
    need = max(FORK_LOG_TO_CANONICAL) + 1
    if len(rtmr0_log) < need:
        raise ValueError(
            f"fork rtmr0_log has {len(rtmr0_log)} events, need >= {need}. "
            "Rebuild the tdx-measure fork with the rtmr0_log field, and confirm "
            "direct-boot mode (kernel/initrd set in the metadata)."
        )
    return {
        canon: bytes.fromhex(rtmr0_log[fork_i])
        for fork_i, canon in FORK_LOG_TO_CANONICAL.items()
    }


# ── Core: splice + replay ─────────────────────────────────────────────────────

def mr1_events(events: list[cc.Event]) -> list[cc.Event]:
    """The MrIndex==1 events that actually fold into RTMR0 (skipping EV_NO_ACTION),
    in order — so list position == the event number in findings §1 (#0..#18)."""
    return [
        e for e in events
        if e.mr_index == 1 and e.event_type != cc.EV_NO_ACTION
    ]


def replay_with_overrides(
    events: list[cc.Event], overrides: dict[int, bytes], alg: int = cc.RTMR_ALG
) -> bytes:
    """Fold RTMR0 (MrIndex==1) substituting overrides[pos] (pos = event number in
    §1) for that event's SHA-384 digest. This is the splice: constant events keep
    their baseline digest, varying events get their recomputed one."""
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


def acpi_digests(acpi_dir: str | Path) -> dict[int, bytes]:
    """{11: SHA384(table-loader), 12: SHA384(rsdp), 13: SHA384(acpi/tables)} from a
    directory of generated (or captured) fw_cfg blobs. This is the #11-13 recompute,
    proven in findings §2: the ACPI DATA digests are literally SHA384 of the bytes."""
    acpi_dir = Path(acpi_dir)
    out: dict[int, bytes] = {}
    for pos, fw_name in ACPI_EVENT_POS.items():
        blob = acpi_dir / ACPI_BLOB_FILES[fw_name]
        if not blob.exists():
            raise FileNotFoundError(f"missing fw_cfg blob for #{pos} ({fw_name}): {blob}")
        out[pos] = hashlib.sha384(blob.read_bytes()).digest()
    return out


# ── Per-topology ACPI generation (build host: tdx-measure + Docker) ────────────

def generate_acpi_blobs(
    metadata: dict, out_dir: Path, *, tdx_measure_bin: str, dist: str, qemu_version: str
) -> dict:
    """Run `tdx-measure --create-acpi-tables` to dump the topology's fw_cfg ACPI
    blobs and its {mrtd, rtmr0}. Mirrors local/scripts/run_validation.sh:43. The
    generated etc/acpi/* land next to `acpi_tables` in the metadata; we read those
    for the #11-13 recompute. Returns the parsed tdx-measure JSON ({mrtd, rtmr0}).

    Runs OFFLINE on any x86-64 Linux with Docker + the fork — no TDX, no GPUs. KVM
    speeds the brief ACPI-gen QEMU run but isn't required; reserve=off (applied by
    platform_tables.MeasurementMetadata) lifts the guest-sized-RAM requirement.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "metadata.json"
    result_path = out_dir / "result.json"
    meta_path.write_text(json.dumps(metadata, indent=2))
    subprocess.run(
        [tdx_measure_bin, "--platform-only", "--json-file", str(result_path),
         "--create-acpi-tables", dist, qemu_version, str(meta_path)],
        check=True,
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
    from chutes.guest.gpu.profiles import GPU_PROFILES

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
    overrides = acpi_digests(fixture)
    spliced = replay_with_overrides(events, overrides).hex().upper()

    # Cross-check: the recomputed #11-13 must equal the captured event digests.
    captured = mr1_events(events)
    ok_acpi = all(overrides[p] == captured[p].digest() for p in ACPI_EVENT_POS)

    print(f"baseline replay   : {expected[:16]}…")
    print(f"spliced replay    : {spliced[:16]}…")
    print(f"#11-13 recompute  : {'MATCH captured' if ok_acpi else 'MISMATCH'}")

    # Also validate the fork-log → override path (what `generate` uses) without
    # needing tdx-measure: synthesize a fork rtmr0_log whose [0,8,9,10] entries are
    # the baseline's own #0/#11/#12/#13, run it through overrides_from_fork_log +
    # replay, and confirm it reproduces the baseline. Proves the index map + splice.
    synth = ["00" * 48] * (max(FORK_LOG_TO_CANONICAL) + 1)
    for fork_i, canon in FORK_LOG_TO_CANONICAL.items():
        synth[fork_i] = captured[canon].digest().hex()
    forkpath = replay_with_overrides(events, overrides_from_fork_log(synth)).hex().upper()
    ok_forklog = forkpath == expected
    print(f"fork-log override : {'MATCH baseline' if ok_forklog else 'MISMATCH'}")

    ok = spliced == expected and ok_acpi and ok_forklog
    if args.expect:
        ok = ok and spliced.startswith(args.expect.upper())
        print(f"expect {args.expect}: {'MATCH' if spliced.startswith(args.expect.upper()) else 'NO MATCH'}")
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _cmd_generate(args: argparse.Namespace) -> int:
    """Generate per-topology RTMR0. --profile <name> does one profile; empty --profile
    does ALL. For each baselined topology, run tdx-measure to get its #0/#11-13
    (rtmr0_log), splice into the baseline CCEL (keeping its #14/#2-4/constants) and
    replay → RTMR0.

    A profile only generates correctly when its (mem,cpu) class matches the baseline's —
    that's where #14 comes from. We check the fork's #0 (= measure_td_hob(memory)) against
    the baseline's #0: a match means same memory class. Profiles whose #0 differs are
    reported as PENDING (need their own captured baseline, or the Phase-2 fork #14) and
    never emitted with a wrong #14. Writes the generated profiles to --output.

    Needs the fork + Docker (offline, any x86-64 Linux — no TDX/GPU)."""
    from chutes.guest.gpu.profiles import GPU_PROFILES
    from topology_spec import build_topology_spec, cpu_args_for_qemu_version
    from platform_tables import MeasurementMetadata

    baseline = cc.parse_event_log(Path(args.baseline).read_bytes())
    baseline_rtmr0 = cc.replay(baseline, 1).hex().upper()
    baseline_tdhob = mr1_events(baseline)[TDHOB_POS].digest()  # #0 = the memory-class fingerprint

    def fork_overrides(profile, fp):
        spec = build_topology_spec(
            profile, fp, cpu_args=cpu_args_for_qemu_version(args.qemu),
            firmware=str(Path(args.bios_dir) / profile.firmware_filename),
        )
        with tempfile.TemporaryDirectory() as td:
            meta = MeasurementMetadata(spec, profile, acpi_tables=str(Path(td) / "acpi.bin")).to_dict()
            out = generate_acpi_blobs(
                meta, Path(td), tdx_measure_bin=args.tdx_measure_bin,
                dist=args.dist, qemu_version=args.qemu,
            )
        return overrides_from_fork_log(out.get("rtmr0_log") or []), out.get("mrtd", "")

    names = [args.profile] if args.profile else list(GPU_PROFILES)
    generated, pending = [], []
    for name in names:
        profile = GPU_PROFILES.get(name)
        if profile is None:
            print(f"unknown profile: {name}", file=sys.stderr)
            return 1
        fps = sorted(profile.baselined_measurements.get(args.qemu, set()), key=str)
        if not fps:
            continue  # nothing baselined for this QEMU version
        entries, class_matches = [], None
        # A profile that can't be generated offline yet (e.g. no pci_bars modeled) must not
        # take down the whole publish — mark it PENDING and keep going, like the class check.
        try:
            for fp in fps:
                overrides, mrtd = fork_overrides(profile, fp)
                if class_matches is None:
                    class_matches = overrides[TDHOB_POS] == baseline_tdhob
                    if not class_matches:
                        pending.append(name)
                        print(f"  {name}: PENDING — memory class (#0) differs from the baseline; "
                              "needs a baseline captured for this class (or the Phase-2 fork #14).",
                              file=sys.stderr)
                        break
                rtmr0 = replay_with_overrides(baseline, overrides).hex().upper()
                key = Topology(name, args.qemu, fp).key()
                entries.append({"topology": key, "rtmr0": rtmr0, "mrtd": mrtd})
                print(f"    {key}  rtmr0={rtmr0[:16]}…"
                      f"{'  (reproduces baseline)' if rtmr0 == baseline_rtmr0 else ''}",
                      file=sys.stderr)
        except Exception as exc:
            pending.append(name)
            print(f"  {name}: PENDING — cannot generate offline: {exc}", file=sys.stderr)
            continue
        if class_matches:
            generated.append({"profile": name, "qemu_version": args.qemu, "rtmr0": entries})

    block = {"version": args.version, "profiles": generated}
    if pending:
        block["pending_profiles"] = sorted(set(pending))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(block, indent=2) + "\n")
    print(f"wrote {out}: {len(generated)} profile(s) generated"
          f"{f', pending: {block['pending_profiles']}' if pending else ''}", file=sys.stderr)
    # Fail only when a specific profile was requested but couldn't be generated.
    return 2 if (args.profile and not generated) else 0


def _cmd_list(args: argparse.Namespace) -> int:
    """List the supported topologies the generator would produce RTMR0 for."""
    for t in enumerate_topologies(args.qemu):
        print(t.key())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("selftest", help="validate splice/replay against a fixture")
    st.add_argument("--fixture", default=str(_HERE.parent.parent / "local" / "acpi_real"),
                    help="capture dir with data/CCEL + fw_cfg ACPI blobs")
    st.add_argument("--expect", default="5FC09D10", help="expected RTMR0 hex prefix")
    st.set_defaults(func=_cmd_selftest)

    ls = sub.add_parser("list", help="list supported topologies")
    ls.add_argument("--qemu", default="10.2.1", help="QEMU version filter")
    ls.set_defaults(func=_cmd_list)

    gen = sub.add_parser(
        "generate",
        help="generate per-topology RTMR0 for a profile (build host: needs the tdx-measure fork + Docker/KVM)",
    )
    gen.add_argument("--profile", default="",
                     help="GPU profile (e.g. RTX_PRO_6000) — must match the baseline's class; "
                          "empty = ALL profiles (each generated only if its class matches a baseline)")
    gen.add_argument("--baseline", required=True,
                     help="baseline CCEL blob (data/CCEL) captured from this profile's debug image")
    gen.add_argument("--version", required=True, help="image version (recorded in the output)")
    gen.add_argument("--output", required=True,
                     help="output JSON, e.g. measurements/<ver>/rtmr0-<profile>.json")
    gen.add_argument("--qemu", default="10.2.1", help="QEMU version key in baselined_measurements")
    gen.add_argument("--tdx-measure-bin", default="tdx-measure", help="path to the tdx-measure fork binary")
    gen.add_argument("--dist", default="ubuntu:26.04", help="ACPI-dump container base image")
    gen.add_argument("--bios-dir", default=str(_HERE.parent.parent / "firmware"),
                     help="directory holding the OVMF firmware (profile.firmware_filename); the fork "
                          "opens the metadata's 'bios' path, so it must resolve absolutely")
    gen.set_defaults(func=_cmd_generate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
