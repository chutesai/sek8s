### Changed
- **`chutes-cvm` is now a real Python package under `src/chutes-cvm/`** (import
  `chutes_cvm`, published to PyPI), instead of a loose module tree on `PYTHONPATH` at
  `host-tools/scripts/chutes/`. The rename from `chutes` to `chutes_cvm` avoids colliding
  with the Chutes platform SDK once installed. The offline measurement engine
  (`guest-tools/measurement/*.py`) moved into `chutes_cvm.measurement`, dropping its
  `sys.path` shims.
- **Host provisioning installs the package** — `host_tools` now runs `setup-chutes-cvm.sh`,
  which `pip install -e`'s the package into a venv and puts the `chutes-cvm` console script on
  PATH (with its deps: pyyaml/jsonschema/substrate-interface). Host ansible + `quick-launch.sh`
  call `chutes-cvm <command>` instead of `python3 -m chutes.guest.*`, so dependency-bearing
  commands (`config`, and the upcoming `preflight`) run with their deps available. The sparse
  checkout now includes `src/chutes-cvm/`. Guest image build keeps `PYTHONPATH` (stdlib commands
  only). Set `CHUTES_CVM_PYPI=1` to install from PyPI instead of the checkout.
### Added
- **`chutes-cvm image-set` / `chutes-cvm config` / `chutes-cvm vfio-wedged`** — the
  image-set manifest tool, the config renderer, and the PCI-passthrough-wedged check are
  now first-class subcommands, so every caller routes through the one console script.
