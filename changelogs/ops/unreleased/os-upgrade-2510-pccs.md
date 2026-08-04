### Fixed

- 25.10 → 26.04 host upgrade no longer stalls on `sgx-dcap-pccs`. Intel's
  `noble`-suite PCCS now depends on `nodejs (>= 22.13)`, which is unsatisfiable
  on 25.10 (questing ships nodejs 20.x), so apt parks it as permanently
  "kept back" and `do-release-upgrade` refuses to proceed (holding the package
  does not help — `do-release-upgrade` also refuses with held packages).
  `pre_2510` now backs up the PCCS artifacts (`config/`, `ssl_key/` — API key,
  token hashes, TLS cert, cached collateral) and removes the package before the
  upgrade; a new `init_2604` hook restores them on the upgraded OS *before*
  `setup-tdx-host` reinstalls PCCS from the `resolute` suite, so the reinstall's
  post-install brings the service up already configured and registration is
  preserved without manual steps. Adds a target-keyed `init_<version>` hook
  phase to the os_upgrade role (runs on the new OS after reboot, before
  `setup-tdx-host`).
