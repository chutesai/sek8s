### Changed

- Build-pipeline-only scripts moved from `guest-tools/scripts/` into their Ansible
  role `files/` (they're invoked exclusively by the build): `compute-rtmr3.sh`,
  `compute-rtmr1-2.sh`, `stage-boot-artifacts.sh`, and `extract-vm-measurements.sh`
  (now a sibling of `stage-boot-artifacts`). Roles run them via `{{ role_path }}/files/`.
  `guest-tools/scripts/` now holds only the standalone release tool `publish-image.sh`.

### Removed

- `guest-tools/scripts/extract-acpi.sh`: dead — the old host-side ACPI dump that had
  to be kept in sync with the launcher by hand. Superseded by the offline generation
  path, which shares the launcher's exact `QemuCommand` (`build_qemu_command` →
  `platform_tables`) and generates ACPI via `tdx-measure --create-acpi-tables`.
- `guest-tools/README.md`: the old manual step-by-step measurement guide, superseded
  by build-integrated `compute-rtmrs` + the `guest-tools/measurement/` tooling. The
  concepts now live in `docs/specs/tdx-measurement-verification.md`.
