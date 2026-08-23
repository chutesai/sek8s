### Changed
- **Consolidated the host entrypoint scripts into the `chutes-cvm` CLI.** The thin wrapper
  scripts `run-td`, `verify-host`, `setup-tdx-host`, `tune-host.sh`, `restore-host.sh` and the
  `host-tools/bin/chutes-*` PATH delegators are removed; their operations are now `chutes-cvm`
  subcommands: `launch`, `verify-host`, `setup-host`, `tune-host`, `restore-host`, `reset-gpus`
  (plus `discover-profile`). Logic still lives in the `chutes_cvm.guest` / `chutes_cvm.host`
  modules; the CLI is a thin front door. `discover-profile.sh` is deliberately kept as a
  standalone script (bundled with the package). Callers invoke the `chutes-cvm` console script
  installed by the package's `install.sh`, rather than the removed `host-tools/bin/` symlinks.
