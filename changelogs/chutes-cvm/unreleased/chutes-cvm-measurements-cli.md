### Added
- **`chutes-cvm measurements`** — offline TDX measurement generation is now a first-class command
  group (`generate` / `list` / `selftest`), forwarding to `chutes_cvm.measurement`. The guest build
  calls the CLI instead of per-register shell scripts + ansible roles:
  - **`generate`** — with no `--register`, computes EVERY register in one POST-LUKS call — mrtd +
    RTMR0 (all topologies) + RTMR1/RTMR2 (the image's staged direct-boot artifacts) + RTMR3
    (mounting the root, unlocking it with `LUKS_PASSPHRASE`) — and writes the version's single
    `measurements.yaml`. This is what the miner-VM build runs; there is no separate pre-LUKS RTMR3
    step.
  - **`generate --register rtmr0`** — just the version-level MRTD + per-topology RTMR0 via the
    tdx-measure fork (offline, any x86-64 Linux — no TDX/GPU) as a JSON block; `--profile` does one,
    empty does all. A standalone partial (a full `generate` computes RTMR0 inline).
  - **`generate --register rtmr3`** — just the version-level RTMR3 (SHA-384 chain over the image's
    `/etc/tdx-measure.conf` files), mounting the root read-only. `LUKS_PASSPHRASE` unlocks an
    encrypted root; always recomputes **fresh** (the real value, no cached/reused fallback). Used by
    the partner GPU-VM build (`tee-gpu-vm.yml`), which has no aggregation.
### Changed
- **The guest build's measurement phase is entirely CLI-owned — no measurement roles.** The
  `compute-rtmr0` / `compute-rtmr1-2` / `compute-rtmr3` / `aggregate-measurements` roles and their
  shell scripts are removed; the miner-VM build calls `chutes-cvm measurements generate` once,
  POST-luks, for every register. The CLI unlocks the encrypted root with `LUKS_PASSPHRASE` to read
  RTMR3's userspace files (always fresh — no cached-value reuse), so there is no longer a separate
  pre-LUKS RTMR3 stage. `libguestfs-tools` (guestmount) is now a build-host prereq
  (`build-setup.yml`); `tee-gpu-vm.yml` calls `measurements generate --register rtmr3` inline. The
  generator's firmware / selftest-fixture paths resolve via `chutes_cvm.paths`.
- **The package-level CLI dispatcher moved to the package root** — `chutes_cvm/guest/cli.py` →
  `chutes_cvm/cli.py` (console script `chutes-cvm` → `chutes_cvm.cli:main`), since it routes to the
  host, guest, and measurement subpackages rather than belonging to `guest/`.
