### Added

- **`chutes-cvm update`** — updates the CLI in place by re-running `install.sh`, so there is no
  second copy of the install logic to drift from how the host was set up. It resolves the install
  mode the same way `version` does: an editable install (the CLI resolves from a checkout) gets a
  `git pull --ff-only` followed by a re-install from that checkout, while a non-editable install
  (the `curl | bash` route discards its source, so there is nothing to pull) fetches the installer
  at `--ref` and runs it. `--ref` defaults to `main`, matching `install.sh`, with a tag as the
  override. Requires root — it writes the venv and the `/usr/local/bin` shim — and says so rather
  than failing with a permission traceback.
  - Deliberately does not check the CLI against the installed guest image: nothing declares which
    CLI versions pair with which image versions, so such a check could only compare two numbers
    with no rule relating them.
  - Hands off with `execv` rather than a subprocess, so the interpreter is replaced instead of
    reinstalling the package it is importing from. Everything printed after the handoff is
    `install.sh`'s output.

### Removed

- The PyPI install path (`CHUTES_CVM_PYPI` / `CHUTES_CVM_VERSION`). It was left over from the
  original consolidation design and could never have worked: the package bundles the NVIDIA GPU
  admin tools wheel, which is not installable as a dependency, and it is paired with firmware that
  ships outside the package entirely. The firmware copy was in fact skipped in that mode — and so
  was the warning about it — so a PyPI install would have succeeded and then failed at VM launch
  with nothing pointing at the cause. `install.sh` now has the two modes it actually supports,
  repo-present (editable) and bootstrap (non-editable), and the now-unused `MODE` variable is gone.

### Fixed

- `install.sh` no longer describes the repository as private, which implied the fetch needs git
  credentials it does not need. `README.md` no longer states the package is published to PyPI, and
  explains why it is deliberately not; the `pyproject.toml` `include` comment no longer justifies
  itself by that path (the `include` is unchanged — the non-editable install needs those files in
  the built wheel).
