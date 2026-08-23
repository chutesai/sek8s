### Added
- **`chutes-cvm generate-measurements`** — offline TDX measurement generation is now a first-class
  subcommand (`generate` / `list` / `selftest`), forwarding to
  `chutes_cvm.measurement.generate_measurements`. `generate` self-generates the version-level MRTD
  and per-topology RTMR0 via the tdx-measure fork (offline, any x86-64 Linux — no TDX/GPU); `--profile`
  does one profile, empty does all. The guest build's `compute-rtmr0` role calls this console-script
  command, and the generator's firmware / selftest-fixture paths resolve via `chutes_cvm.paths`
  (`firmware_dir()` / `repo_root()`).
