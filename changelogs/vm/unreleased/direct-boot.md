### Added

- **Chute log shipper (guest side, Phase 1).** New `chute-log-shipper` systemd service in the
  attested guest image that closes the gap where a chute crashing before instance registration
  left its logs unreachable. It discovers chute pods locally via the CRI socket
  (`k3s crictl pods -o json`, through a restricted `crictl-pods-helper` wrapper), reads their logs
  off `/var/log/pods/chutes_*/…`, and streams them to the validator over the per-boot registry mTLS
  leaf. Runs as a dedicated non-root uid, confined by the `sek8s.chute-log-shipper` AppArmor profile
  (only the chute log paths, the CRI socket, the registry-tls leaf, the cursor dir, and egress).
  - New Python package `sek8s.log_shipper` (`config`, `crictl`, `checkpoint`, `shipper`, `agent`,
    `exceptions`) with a `chute-log-shipper` console entry; no new dependencies (reuses `aiohttp` +
    the `run_command` shell pattern, no k8s API access).
  - **Streaming read path (deterministic memory).** Each pod is captured by a single streaming
    coroutine that tails only *new* bytes from a bounded `buffer_bytes` window (byte offset per log
    file keyed by inode so it follows kubelet rotation; reset on truncation), rather than re-reading
    whole files. Memory is bounded to `buffer_bytes × pods` (≤ 1 chute pod per GPU); a slow validator
    pauses reading (backpressure); only complete logical lines are shipped (window-cut lines and CRI
    `P`-runs are held); the shipped byte offset is committed on success and persisted to a
    `{config_id → {inode → offset}}` checkpoint for restart resume. No wall-clock capture backstop —
    termination is the validator's job (`204`).
  - New Ansible role `chute-log-shipper` (registered in `chutes-miner-vm.yml`) plus the AppArmor
    profile delivered via `apparmor-hardening`. No new leaf is minted — a boot-time path unit
    re-groups the existing per-boot CVM mTLS leaf for the service's uid.
  - Wire contract: `POST https://cvm.chutes.ai/instances/launch_config/{config_id}/logs` with a
    body of `{"deployment_id": "<uuid>", "logs": [{ts, stream, log}]}`. Identity is derived
    validator-side from the mTLS leaf + path + proxy — nothing security-relevant is self-asserted;
    `deployment_id` (from the `chutes/deployment-id` pod label) is the sole top-level field, non-
    security correlation metadata the validator cannot derive from `config_id` pre-registration.
    `204` = streaming terminated by the validator (stop); other 2xx = keep sending; `403`/`404` =
    streaming rejected (stop + log the reason: `404` unknown config_id, `403` cert/ownership) — no
    indefinite retry; any other non-2xx / connection error = transient retry with backoff. Dedupe is
    `(config_id, ts)`, so no `seq` is sent (deviation from the original spec, which listed `seq`).

### Changed

- **CVM mTLS client cert CN generalized.** The per-boot mTLS client leaf minted by the vm-tls
  initramfs `setup_vm_tls` script now uses a generic subject (`CN=sek8s-cvm-mtls-client`) instead of
  `sek8s-vm-registry-client`. That leaf is the shared identity for *all* CVM mTLS (registry pulls,
  the log shipper, …), not registry-specific, so the old name was misleading. Identity is **not**
  carried in the CN — the validator resolves `(miner_hotkey, vm_name)` by verifying the leaf against
  the registered per-boot VM CA — so the CN is intentionally generic, not per-VM. Edits initramfs →
  shifts **RTMR2**; regenerate measurement baselines before rollout.
