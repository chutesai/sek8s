# Measurement data (baselines + generated outputs)

Per-version measurement artifacts, kept separate from the tooling in
`guest-tools/measurement/`. One subdir per guest image version:

- `<version>/` — captured baseline (CCEL + fw_cfg ACPI/SMBIOS preimages,
  `baseline.json`) produced by the `capture-ccel` role (the final
  gather step (`capture-ccel`) during a build; re-run via `--tags gather-measurement-inputs`),
  plus the generated `teeMeasurements` block for that version.

Committed reference data — small firmware/ACPI/SMBIOS preimages only. The
captured baseline holds only the **RTMR0** inputs (the debug CCEL splice + the
per-topology ACPI/SMBIOS preimages), which are identical across debug and prod.
**RTMR1/2/3** are not captured here; they are computed statically from each image at
build time (`compute-rtmr3` pre-luks; `compute-rtmr1-2` + `compute-rtmr0` post-luks) for **both**
prod and debug builds — the debug image is attested too under the RC gate (its
distinct measurement is registered `rc:true`).
