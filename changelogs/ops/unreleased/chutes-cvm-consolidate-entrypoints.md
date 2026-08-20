### Changed
- **Consolidated the host entrypoint scripts into the `chutes-cvm` CLI.** The thin wrapper
  scripts `run-td`, `verify-host`, `setup-tdx-host`, `tune-host.sh`, `restore-host.sh` and the
  `host-tools/bin/chutes-*` PATH delegators are removed; their operations are now `chutes-cvm`
  subcommands: `launch`, `verify-host`, `setup-host`, `tune-host`, `restore-host`, `reset-gpus`
  (plus `discover-profile`). Logic still lives in the `chutes.guest` / `chutes.host` modules;
  the CLI is a thin front door. `discover-profile.sh` is deliberately kept as a standalone
  script. Ansible invokes the bootstrap-free `python3 -m chutes.guest.cli <cmd>` form (no venv
  needed); host setup installs the `chutes-cvm` shim via `setup-chutes-cvm.sh` instead of
  symlinking `bin/`.
