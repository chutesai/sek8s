### Added
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
