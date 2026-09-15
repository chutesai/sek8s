### Added
- `/opt/sek8s/venv` is measured into RTMR3. It is the interpreter and dependency tree every sek8s
  service executes through (`attestation-service`, `admission-controller`, `system-manager`,
  `system-status`, `chute-log-shipper`), so while `/opt/sek8s/src` pinned our own code, the
  third-party packages underneath it were unmeasured — offline tampering there is code execution
  inside the attestation service with the measured registers unchanged, since a quote is only as
  meaningful as the code that chooses its `REPORTDATA`.
- `/etc/overlayroot.conf` and `/etc/overlayroot.local.conf` are measured. Both are removed or
  neutralised at build time; a missing path contributes nothing to RTMR3, so measuring them means
  a reappearance changes the measurement and fails the boot gate.

### Changed
- The guest venv is now byte-reproducible, which is what made measuring it possible. Three inputs
  were unpinned:
  - **Dependency versions.** `poetry.lock` was copied into the build and never used — the install
    resolved from `pyproject.toml` caret ranges, so the measurement was a function of what the
    index served on build day. `scripts/generate_guest_requirements.py` now derives an exact,
    hash-pinned requirements file from the lock (`make guest-requirements`), and the guest installs
    it with `--require-hashes`, so a substituted artifact fails the build instead of silently
    moving RTMR3. Only the guest packages' transitive closure is emitted, computed rather than
    assumed so the guest cannot inherit a dependency added for another workspace package.
  - **Build tooling.** `pip`, `setuptools` and `wheel` were installed `state: latest`. `pip` and
    `poetry-core` are now pinned explicitly; `setuptools` and `wheel` come from the lock. The
    editable installs use `--no-build-isolation` so the build backend is the pinned `poetry-core`
    rather than an unpinned copy fetched at build time.

    `poetry-core` is pinned to one version across every `[build-system] requires` in the repo,
    and the guest install matches it. A build backend is not a locked dependency, so pip's build
    isolation fetched whichever version was current on build day and stamped it into
    `dist-info/WHEEL` — a venv could end up holding artifacts from two different backends
    (`sek8s` built by 2.4.1 next to `sek8s-common` built by 2.3.2), which lands directly in the
    measured bytes.
  - **Bytecode.** `.pyc` files embed the source mtime in their PEP 552 header under the default
    timestamp invalidation, so identical sources produced different bytes. They are now compiled in
    one pass with `--invalidation-mode unchecked-hash`, which embeds no mtime. That mode also means
    Python never revalidates a `.pyc` against its `.py`, so nothing rewrites them at runtime and the
    measurement survives a reboot — and the `.pyc`, not the `.py`, is what executes.

  Verified by building the full recipe twice in `ubuntu:24.04` at the guest's real paths, separated
  in time: 9,558 byte-identical files.

### Removed
- The `copymods` and `cloud-initramfs-dyn-netconf` initramfs hooks, alongside the `overlayroot` and
  `mdadm` hooks already dropped. Both sort ahead of `rtmr3-measure` and neither is used: `copymods`
  exits early when the root already has modules for the running kernel, and guest networking is
  static. Removed as unused mount and write primitives in the measured boot path rather than as
  exposures — both read only RTMR2-covered inputs.
- `/etc/overlayroot.local.conf`. No package in the archive ships it; the Ubuntu cloud image creates
  it setting `overlayroot_cfgdisk=LABEL=OROOTCFG`. Config precedence in overlayroot's `init-bottom`
  script is `20-root-config` < `30-root-local-config` < `80-cfgdisk`, so that setting lets a config
  disk the host attaches outrank both on-root configs — reaching the overlay without the kernel
  cmdline (measured into RTMR2) or the LUKS root. Removing the hook already closed the path; this
  disarms the config so a future initramfs regeneration cannot re-arm it.
  `/etc/overlayroot.conf` is deliberately kept: it is a dpkg conffile and is what asserts
  `overlayroot_cfgdisk="disabled"`.
