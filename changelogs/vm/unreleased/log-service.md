### Added

- **Chute log shipper service (guest image, Phase 1).** New `chute-log-shipper` systemd service +
  Ansible role in the attested guest image, running the `sek8s.log_shipper` agent as a dedicated
  non-root uid. Ships crash/warmup logs of chute pods to the validator before instance registration.
  - New Ansible role `chute-log-shipper` (registered in `chutes-miner-vm.yml`): hardened systemd
    unit, rendered env, a restricted `crictl-pods-helper` wrapper (read-only `pods`/`ps` JSON), the
    dedicated uid, and boot wiring (group/ACL for the CRI socket + `/var/log/pods` + the registry-tls
    leaf, cursor/checkpoint state dir). No new leaf is minted — a boot-time path unit re-groups the
    existing per-boot CVM mTLS leaf for the service's uid.
  - `sek8s.chute-log-shipper` AppArmor profile delivered via `apparmor-hardening`, confining the
    service to the chute log paths, the CRI socket, the registry-tls leaf, the checkpoint dir, and
    egress to the validator.
  - **Measurement:** adds guest image content (package + systemd unit + crictl wrapper + AppArmor
    profile) → shifts **RTMR3**. Regenerate expected-measurement baselines before rollout.

### Changed

- **CVM mTLS client cert CN generalized.** The per-boot mTLS client leaf minted by the vm-tls
  initramfs `setup_vm_tls` script now uses a generic subject (`CN=sek8s-cvm-mtls-client`) instead of
  `sek8s-vm-registry-client`. That leaf is the shared identity for *all* CVM mTLS (registry pulls,
  the log shipper, …), not registry-specific, so the old name was misleading. Identity is **not**
  carried in the CN — the validator resolves `(miner_hotkey, vm_name)` by verifying the leaf against
  the registered per-boot VM CA — so the CN is intentionally generic, not per-VM. Edits initramfs →
  shifts **RTMR2**; regenerate measurement baselines before rollout.
