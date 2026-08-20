### Added
- **`chutes-cvm` CLI (seed).** A stdlib-based Python CLI (`chutes.guest.cli`) as the eventual
  single entry point for confidential-VM host operations, growing gradually to subsume the
  host-tools bash scripts. First command: `chutes-cvm verify-host` (wraps the existing
  host-readiness gates with a colored, TTY-aware result banner; `--target-os` for a
  pre-upgrade check). No new runtime dependency — verify-host is pure stdlib.
- **`host-tools/provision/setup-chutes-cvm.sh`.** Idempotent, self-contained bootstrap: creates
  a venv with the CLI's deps and installs the `chutes-cvm` shim (`→ python3 -m chutes.guest.cli`)
  pointing at this checkout. A miner can run it directly (no Ansible required), and Ansible
  host-setup can invoke the same script — one source of truth for CLI setup. Paths are
  overridable via `CHUTES_CVM_VENV` / `CHUTES_CVM_BIN`.
