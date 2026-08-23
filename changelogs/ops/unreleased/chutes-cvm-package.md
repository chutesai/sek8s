### Changed
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
### Added
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
