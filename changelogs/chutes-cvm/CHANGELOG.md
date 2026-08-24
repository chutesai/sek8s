# chutes-cvm Changelog

The `chutes-cvm` CLI + toolkit (`src/chutes-cvm/`) — an independently installable host CLI
(`pip`/`install.sh`). Versioned with SemVer via `src/chutes-cvm/VERSION`. Run
`make promote-changelogs` to aggregate fragments into the current version section.
## [0.1.0] - 2026-08-24

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
- **`src/chutes-cvm/install.sh` — the single source of truth for install** (replaces
  `host-tools/scripts/provision/setup-chutes-cvm.sh`). One script owns both fetch and install, and
  picks its mode: run from a checkout (ansible / build / dev) → editable install from that checkout
  (a `git pull` updates the code, no reinstall); `curl -sSL …/src/chutes-cvm/install.sh | bash` →
  sparse shallow-fetch (`host-tools/` + `firmware/` + `src/chutes-cvm/`) into a **temporary** dir,
  non-editable install (CLI + bundled nvidia-gpu-tools into a persistent venv, firmware copied next
  to it), then delete the temp checkout. The sparse path-list and the install steps live here
  exactly once; the `host_tools` ansible role and the guest build both invoke it. A standalone
  install is fully launch-capable — no manual `git clone`, no lingering source, no PyPI, no R2.
  `chutes-cvm setup-host` no longer installs/verifies the CLI or gpu-tools (install.sh installs
  them; the launch path verifies gpu-tools where it matters); its `--install-tools-only` flag and
  the `install_dependencies` step are removed.
- **`make bundle-gpu-tools`** — discoverable maintainer target that rebuilds the vendored
  nvidia-gpu-tools wheel into the package (recipe at `src/chutes-cvm/tools/gpu-tools/`).
- **`chutes-cvm image-set` / `chutes-cvm config` / `chutes-cvm vfio-wedged`** — the
  image-set manifest tool, the config renderer, and the PCI-passthrough-wedged check are
  now first-class subcommands, so every caller routes through the one console script.
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
- **Consolidated the host entrypoint scripts into the `chutes-cvm` CLI.** The thin wrapper
  scripts `run-td`, `verify-host`, `setup-tdx-host`, `tune-host.sh`, `restore-host.sh` and the
  `host-tools/bin/chutes-*` PATH delegators are removed; their operations are now `chutes-cvm`
  subcommands: `launch`, `verify-host`, `setup-host`, `tune-host`, `restore-host`, `reset-gpus`
  (plus `discover-profile`). Logic still lives in the `chutes_cvm.guest` / `chutes_cvm.host`
  modules; the CLI is a thin front door. `discover-profile.sh` is deliberately kept as a
  standalone script (bundled with the package). Callers invoke the `chutes-cvm` console script
  installed by the package's `install.sh`, rather than the removed `host-tools/bin/` symlinks.
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
- **`chutes-cvm` is now a real Python package under `src/chutes-cvm/`** (import
  `chutes_cvm`, published to PyPI), instead of a loose module tree on `PYTHONPATH` at
  `host-tools/scripts/chutes/`. The rename from `chutes` to `chutes_cvm` avoids colliding
  with the Chutes platform SDK once installed. The offline measurement engine
  (`guest-tools/measurement/*.py`) moved into `chutes_cvm.measurement`, dropping its
  `sys.path` shims.
- **Host provisioning installs the package** — `host_tools` stages and runs the package's
  `install.sh`, which fetches + `pip install -e`'s the package into a venv and puts the
  `chutes-cvm` console script on PATH (with its deps: pyyaml/jsonschema/substrate-interface). Host
  ansible + `quick-launch.sh`
  call `chutes-cvm <command>` instead of `python3 -m chutes.guest.*`, so dependency-bearing
  commands (`config`, and the upcoming `preflight`) run with their deps available. The sparse
  checkout now includes `src/chutes-cvm/`. Guest image build keeps `PYTHONPATH` (stdlib commands
  only). Set `CHUTES_CVM_PYPI=1` to install from PyPI instead of the checkout.
- **The package is self-contained — no repo-layout assumptions.** The built nvidia-gpu-tools
  wheel moved into the package (`chutes_cvm/scripts/gpu-tools/`, bundled in the wheel; its
  maintainer build recipe lives at `src/chutes-cvm/tools/gpu-tools/`, run via `make
  bundle-gpu-tools`, and builds the wheel into the package), and the default launch-config lookup
  is now `./config.yaml` (where
  `chutes-cvm init` writes it) / `$CHUTES_CVM_CONFIG` rather than a checkout path. The only
  checkout-relative resolution left is the guest firmware (OVMF) — MRTD-measured, so intentionally
  not shipped in this host-side package. A repo-present (editable) install resolves it from the
  checkout; a standalone (non-editable) install copies it out of the fetched checkout to a
  persistent dir and sets `$CHUTES_CVM_FIRMWARE_DIR` in the shim, so no repo or R2 is needed at
  runtime.
- **nvidia-gpu-tools is installed at CLI-setup time, not lazily at launch.** `install.sh`
  `pip install`s the bundled wheel into the chutes-cvm venv and symlinks `nvidia-gpu-tools` on
  PATH; the runtime lazy self-installing venv machinery is removed (`chutes_cvm.guest.gpu.tools`
  now only verifies the CLI is present and runs, raising a clear "re-run install.sh" error).
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

