### Changed
- **`chutes-cvm setup-host` is now the complete per-host configuration.** It folds in what were
  three ansible roles so that running the CLI fully provisions a host (launch-ready modulo the CLI
  install itself, PCCS secrets, and a reboot): the `ntp` role becomes `_setup_ntp()` (chrony with
  `makestep` to step a skewed BMC RTC before any VM inherits the host clock), the `chutes_dirs` role
  becomes `_ensure_chutes_dirs()` (`/var/lib/chutes/base-images` + `vm-overlays`), and the host
  operational deps from `host_prerequisites` (chrony, aria2, xfsprogs, python3-yaml) move into a new
  version-independent `HostProfile.base_packages` installed alongside the kernel + TDX stack.
- **The `setup.yml` host-setup playbook is now thin orchestration.** It runs only `host_tools`
  (bootstraps the CLI), `tdx_bootstrap` (`chutes-cvm setup-host` + reboot + TDX-init verify), and
  `pccs_configure` (vault-held PCCS secrets) — the boundary is: the CLI owns per-host config,
  ansible owns bootstrap, secrets, and fleet reboot/verify. The `host_tools` role now self-ensures
  its own install prerequisites (python3-venv/pip **+ git** for the sparse fetch), so no separate
  pre-CLI `host_prerequisites` step is needed in setup.
### Removed
- **The `ntp` and `chutes_dirs` ansible roles** — folded into `chutes-cvm setup-host` (above). The
  `host_prerequisites` role stays (still used by the launch / remediate / build-setup playbooks) but
  is no longer part of `setup.yml`.
