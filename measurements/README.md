# Measurement data (baselines + generated outputs)

Per-version measurement artifacts, kept separate from the tooling in
`guest-tools/measurement/`. One subdir per guest image version:

- `<version>/` — captured baseline (CCEL + fw_cfg ACPI/SMBIOS preimages,
  `baseline.json`) produced by `ansible/guest/playbooks/capture-measurement-baseline.yml`,
  plus the generated `teeMeasurements` block for that version.

Committed reference data — small firmware/ACPI/SMBIOS preimages only. The
captured baseline holds only the **RTMR0** inputs (the debug CCEL splice + the
per-topology ACPI/SMBIOS preimages). **RTMR1/2/3** are not captured here; they are
computed from the **prod** image at build time (`compute-rtmr3` and the build-time
rtmr1/2 step), because the debug initrd differs from prod.
