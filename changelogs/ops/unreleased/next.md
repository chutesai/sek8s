### Removed
- **Ubuntu 25.10 host support — 26.04 is the only supported host OS.**
  - `support_matrix.py` and the host-tools README table list 26.04 only (H200 /
    B200 / RTX Pro 6000, 8-GPU); the three `25.10` rows are gone, so
    `setup-tdx-host --topology-matrix` shows 26.04 exclusively.
  - `Ubuntu2510Profile` and its `HOST_PROFILES["25.10"]` entry are deleted:
    `setup-tdx-host` now fails with "Unsupported Ubuntu version" on a 25.10 host
    instead of provisioning it.
  - `SUPPORTED_QEMU_BY_OS` drops `25.10 -> 10.1.0`, and the `"10.1.0"` keys are
    removed from `baselined_measurements` (H200, B200_XEON6, RTX_PRO_6000). A host
    still on 25.10 is refused at launch by the QEMU host-readiness gate, and
    `verify-host` reports BLOCKED. This also retires the fingerprints that were
    only ever baselined at 10.1.0 — flat-path H200 (>2 NUMA nodes) and SNC3
    B200_XEON6 — since neither has a registered 10.2.1 RTMR0 and so could not
    attest on 26.04 anyway.

### Changed
- The 25.10 → 26.04 upgrade path is untouched: `upgrade-host.yml`, the
  `os_upgrade_path` hops, and the `pre_2510` / `init_2604` hooks all still run, so
  existing hosts can advance. 25.10 is now an upgrade waypoint only — from 25.04
  always run with `-e target_version=26.04`, since a run whose final hop is 25.10 is
  refused by the `verify-host` pre-flight (no baselined QEMU for that release).
