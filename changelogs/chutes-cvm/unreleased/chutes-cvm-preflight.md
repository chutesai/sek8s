### Added
- **`chutes-cvm launch`** — end-to-end VM launch orchestrator (verify host → volumes → network →
  boot) from `config.yaml`. This is the one command a miner uses to bring a VM up. It calls the
  low-level QEMU primitive, now the hidden `chutes-cvm launch-vm`.
- **`chutes-cvm download` / `init` / `stop` / `down`** — the quick-launch modes that used to be
  flags are now first-class commands: `download [--debug]` fetches + verifies a base image set,
  `init` scaffolds a `config.yaml`, `stop` stops only the VM (leaving the bridge up), and `down`
  tears the whole environment down (VM + bridge + benchmark-netlog).
- **`chutes-cvm preflight`** — asks the control plane whether this host class can launch. Captures
  the host's platform metadata (discover-profile), signs it with the miner hotkey (sr25519), and
  POSTs it to the API, which owns the fingerprint and returns accepted / pending / unknown. Submits
  the profile when unknown (unless `--dry-run`). Exit 0 accepted / 1 error (fail-closed) / 2 not-yet.
  Adds `substrate-interface` to the chutes-cvm package for the signature.
### Changed
- **`chutes-cvm verify-host` is now API-backed.** Gate A (host runs its OS release's QEMU) stays
  local; Gate B (is this host class attestable?) is a dry-run preflight against the control plane
  instead of the in-repo `known_topologies` set. `--target-os` swaps in the target OS's QEMU before
  the API fingerprints the profile. Fails closed (BLOCKED) when it can't get a verdict.
- **`detect_profile` no longer gates on a local baselined set.** It resolves the GPU profile and the
  live fingerprint (which still drive the launch `-smp`/`-m`); acceptance is the control plane's call.
- **VM-management scripts now ship inside the `chutes-cvm` package.** `quick-launch.sh`,
  `prepare-vm-image.sh`, `discover-profile.sh`, and the `volumes/`, `network/`, `devices/`, and
  `config/` (schemas) helpers moved from `host-tools/scripts/` into `chutes_cvm/scripts/`, resolve
  package-relative, and are bundled in the wheel. `host-tools/scripts/` now holds only config
  examples. Ansible host launch/upgrade and the capture-ccel measurement role invoke
  `chutes-cvm launch` instead of `./quick-launch.sh`; the install no longer needs a
  `CHUTES_CVM_SCRIPTS_DIR` env (the scripts are package-relative).
- **`quick-launch.sh` shrank to pure orchestration.** Its `--download` / `--download-debug` /
  `--template` / `--clean` early-exit modes moved out to the `download` / `init` / `down` commands
  above, and its final step now calls `chutes-cvm launch-vm`. The `config.tmpl.yaml` template moved
  into the package (so `chutes-cvm init` can emit it); the config `.example.yaml` files stay in
  `host-tools/scripts/config/`. Guest roles that drove the primitive directly (prime-vm) now call
  `chutes-cvm launch-vm` / `chutes-cvm stop`.
