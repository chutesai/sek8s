### Added

- New Ansible role `apparmor-hardening`: installs AppArmor profiles, abstractions, systemd drop-ins, and a boot-time profile verification service (`lock-mac-caps.service`).
- AppArmor abstraction `sek8s-cache-deny`: denies shell/interpreter access to the HF model cache volume (`/var/snap/cache/`). Debug builds use `audit deny` for kernel audit logging; production builds use silent `deny`.
- AppArmor abstraction `sek8s-secrets-deny`: denies shell/interpreter access to boot secrets (`/run/chutes/`), containerd socket, k3s token, and miner credentials.
- AppArmor profile `sek8s.system-manager`: named profile applied via systemd `AppArmorProfile=` — grants cache rw, credential read, containerd socket, and network access.
- AppArmor profile `sek8s.setup-cache`: named profile for the setup-cache service — grants cache rw and coreutils, no network or credentials.
- AppArmor profile `sek8s.deny-sensitive-default`: auto-attaches to common shells, interpreters, and data-transfer tools (bash, dash, sh, cat, cp, tar, rsync, curl, wget, perl, etc.) — includes both deny abstractions to block access to protected paths.
- `verify-apparmor-profiles.service`: oneshot that verifies all sek8s AppArmor profiles are loaded in enforce mode at boot. Powers off the VM on failure.
- RTMR3 progress logging: per-directory collection progress and periodic hashing progress (every 200 files) logged to `/dev/kmsg` during the expanded measurement phase.

### Changed

- `tdx-measure-miner.conf`: added three-tier RTMR3 measurement expansion — Tier 1 (custom binaries in `/usr/local/{bin,sbin}`), Tier 2 (code injection config paths), Tier 3 (system binaries in `/usr/bin`, `/usr/sbin`, and custom shared libs in `/usr/local/lib`). Also added service configs, AppArmor profiles, and systemd units not previously measured.
- `tdx-measure-miner.conf`: removed `/etc/rancher/k3s/registries.yaml` (runtime-modified by `process-config.py`, persists across reboots; security properties independently measured through other files).
- `pods.rego`: added `MAC_ADMIN` and `MAC_OVERRIDE` to `dangerous_capabilities` to prevent containers from modifying AppArmor profiles.
- `chutes-miner-vm.yml`: inserted `apparmor-hardening` role after `cache-volume` and before dynamic config services.
