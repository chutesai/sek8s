### Added

- `guest-tools/measurement/ccel_replay.py`: parser and SHA-384 replay for the TDX CC
  event log (`/sys/firmware/acpi/tables/data/CCEL`). Reconstructs each RTMR from
  the TCG_PCR_EVENT2 records and cross-checks against a live quote, discovering
  the MrIndex→RTMR mapping empirically. Includes a `diff` subcommand to compare
  two CCELs per-register (which events are constant across hosts vs which vary by
  topology). Phase-1 tooling for offline measurement work (determining RTMR0
  without booting each topology on hardware).
- `guest-tools/measurement/`: home for the offline RTMR0 measurement/verification
  tooling (event-log parse/replay, byte-diff, SMBIOS matcher), which reuses the
  launcher's QEMU-arg builders (`chutes.guest.qemu`) and the `virtee/tdx-measure`
  engine (chutesai fork). See its `README.md` for the dependency + build steps.
- `docs/specs/tdx-measurement-verification.md`: spec describing how TDX guest
  measurements (MRTD/RTMR0–3) are structured, what makes `rtmr0` the only
  per-topology register (and hardware-independent), and how they can be
  independently reproduced and verified with the `guest-tools/measurement/`
  tooling.

- `guest-tools/measurement/utils/smbios_match.py`: reverse-engineers the exact
  preimage of the RTMR0 SMBIOS handoff event (#14) by matching `SHA384`
  candidates (fw_cfg `smbios-tables`/`smbios-anchor`, `/sys` DMI, concatenations)
  against the real digest in a captured CCEL — the last per-topology event whose
  formula wasn't yet pinned.
- `guest-tools/measurement/utils/acpi_bytediff.py`: parses a fw_cfg
  `etc/acpi/tables` blob into its constituent tables and byte-diffs two blobs
  per-table, so a QEMU-generated blob can be validated against a real dump and
  any mismatch localized (e.g. DSDT pci-hole/phys-bits vs SRAT topology).

### Changed

- `guest-tools/measurement/extract-measurements.sh`: also capture the CC event-log data blob
  (`/sys/firmware/acpi/tables/data/CCEL`) alongside the CCEL ACPI table, plus the
  fw_cfg ACPI/SMBIOS preimages (`etc/acpi/tables`, `etc/table-loader`,
  `etc/acpi/rsdp`, `etc/smbios/smbios-tables`, `etc/smbios/smbios-anchor`) and
  `/sys/firmware/dmi/tables/*`, so the RTMR0 ACPI (#11-13) and SMBIOS (#14) events
  can be reproduced/validated offline.
