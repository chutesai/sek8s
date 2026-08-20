### Added
- **`chutes-cvm discover-profile`.** New CLI command that captures this host's GPU/CPU/NUMA
  profile (delegating to `discover-profile.sh` for now), so `chutes-cvm` is the front door
  for both host inspection commands (`verify-host`, `discover-profile`). `--json-only` /
  `--no-json` forward to the underlying script.
