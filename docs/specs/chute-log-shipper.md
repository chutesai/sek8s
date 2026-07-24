# Feature Spec: Chute Log Shipper (guest side)

**Date**: 2026-07-23
**Status**: draft

---

## Context

When a chute crashes or errors **before its instance is registered in the validator**, its logs
are unreachable. Every current log path needs the validator to know the chute's `host:port`, which
only exists once an `Instance` row is created — on launch-config *claim*, after verification. A
chute that dies before claim leaves a `LaunchConfig` with `failed_at` and **no Instance**, so the
miner CLI and the validator's `encrypted_logs` capture both have nothing to read. And because a
launch config is not tied to a server, the validator cannot even know which node to look at — only
an **in-cluster** component (with cluster-wide API visibility) can see the pod in time.

This spec covers the **guest-side agent** that closes that gap: a single in-cluster Deployment in
the attested guest image that watches chute pods, reads their logs via the k8s API, and streams
them outbound to the validator, which caches them and controls when capture stops. The validator
side is specified separately in [chute-log-shipper-validator-api.md](chute-log-shipper-validator-api.md)
(portable, to be implemented in `chutes-api`).

Delivered in two phases so the gap fix ships without depending on the riskier retirement of the
per-chute 8001 log server (Phase 2).

- **Packages affected**: `src/sek8s/` (new agent package), `ansible/guest/`
- **Key files**:
  - `src/sek8s/sek8s/log_shipper/` (new — agent package: `config.py`, `agent.py`, `shipper.py`,
    console entry in `src/sek8s/pyproject.toml`), mirroring the `system_manager` layout
  - `ansible/guest/roles/chute-log-shipper/` (new — RBAC manifest template + env + rollout), or fold
    the manifest into the existing attestation-service manifest role
  - `ansible/guest/roles/.../templates/*.yaml.j2` — the ServiceAccount + Role/RoleBinding +
    Deployment manifest, delivered via `/var/lib/rancher/k3s/server/manifests/` (measured, immutable)
  - `ansible/guest/roles/rtmr3-measure/files/tdx-measure-miner.conf` (already covers
    `/var/lib/rancher/k3s/server/manifests`; confirm the new manifest is in the measured set)
  - `ansible/guest/playbooks/chutes-miner-vm.yml` (register the new role)
  - `changelogs/vm/unreleased/direct-boot.md` (`### Added` fragment)
- **Reused, unchanged**:
  - `/run/chutes/registry-tls/client.{crt,key}` — the per-boot registry mTLS leaf minted by
    `ansible/guest/roles/vm-tls/files/initramfs/setup_vm_tls` (egress credential; **no change** to
    `setup_vm_tls`, so RTMR2/initramfs is untouched)
  - `ansible/guest/roles/attestation-service/templates/proxy-manifests.yaml.j2` — reference for the
    `Role`/`RoleBinding`/manifest-delivery + hostPath-cert-mount pattern
  - `src/sek8s/sek8s/system_manager/status/util.py` (`run_command`) — safe `create_subprocess_exec`
    allowlist pattern if shelling to `kubectl`
- **Dependencies / external** (out of scope here, required for the feature to function):
  - `chutes-api`: the ingest/cutoff/storage/read endpoints in the validator spec
  - `chutes-miner-cli`: `instance-logs` fallback to the cached logs (validator spec §CLI)

---

## Design Decisions

- **Source = `pods/log`, not the 8001 log server.** The chute logs to stdout
  (`chutes/entrypoint/_shared.py:148`), and the mandatory `chutes-aegis.so` `LD_PRELOAD` shim tees
  stdout into `/tmp/_chute.log` **and** passes through to real stdout → the kubelet captures it. So
  `pods/log` already carries the same content the 8001 server serves, **including after a crash**
  (Failed pods, `restartPolicy=Never`, persist). No chute-lib change is needed to read logs this way.
- **Single in-cluster Deployment (not a DaemonSet).** `pods/log` is proxied cluster-wide by the API
  server, so one agent sees every chute pod; each guest is a single-node k3s cluster anyway. A
  DaemonSet would buy nothing here. Revisit only if a guest cluster ever becomes multi-node *and*
  load distribution matters.
- **Push, with validator-controlled cutoff (simple).** The agent streams batches outbound and keys
  off the response **status code** to decide whether to keep sending or stop — a specific code =
  stop. All cutoff *logic* lives in the API (default: stop at activation; per-chute override keeps
  it going). The agent holds no cutoff policy of its own — it obeys the code — plus a local
  max-duration backstop so a never-activating, never-terminating pod cannot stream forever.
- **Egress auth = mTLS, enforced.** Reuse the per-boot registry mTLS leaf (no new leaf, no
  `setup_vm_tls` edit → no RTMR2 shift). The validator verifies the presented leaf against the
  registered per-boot VM CA → `(miner_hotkey, vm_name)`, binding each shipment to the attested boot.
  There is no non-mTLS path.
- **Scoped ServiceAccount, attested RBAC ceiling.** The agent runs as a dedicated `ServiceAccount`
  (not the `miner` k8s user, so it is not narrowed by the admission-controller authorizer) with a
  namespaced `Role` in `chutes` granting only `pods` get/list/watch + `pods/log` get. The Role is
  shipped as a measured auto-deploy manifest, so the credential's ceiling is in RTMR3.
- **The k8s admin kubeconfig is purged at boot** — the agent must use its own SA token (projected,
  audience-bound preferred), never `/etc/rancher/k3s/k3s.yaml`.

---

## API Changes

The guest agent is a **client**; it calls the validator. No inbound API is added on the guest.
- **Calls out**: the log-ship endpoint (validator) presenting the mTLS leaf. **Must target the
  validator's mTLS-terminating host — `cvm.chutes.ai`** (the new vendor-neutral CVM mTLS domain;
  same GCP LB/nginx that currently answers `tdx-attestation.chutes.ai`), **not** plain
  `api.chutes.ai`, which does not verify client certs. This new agent ships to `cvm.chutes.ai`;
  the legacy `tdx-attestation.chutes.ai` remains only for already-booted old VMs (see validator spec
  §2 backward-compat). Exact path is a validator-side decision (`/servers/{vm_name}/…` preferred).
- **Schema changes**: none in this repo.
- **Migrations**: none.

---

## Goal

Success (Phase 1) =
- The agent runs as a single-replica Deployment in the guest, authenticating to the local k8s API
  with a scoped SA token; `kubectl auth can-i` confirms `get pods/log -n chutes` = yes, `get
  secrets` / other namespaces = no.
- For a chute that **crashes during warmup and never registers an instance**, its logs are shipped
  to the validator and become retrievable (via `chutes-miner instance-logs` and the support view).
- For a chute that **activates normally**, the validator returns `stop` and the agent ceases
  capture for that pod — no steady-state logs are shipped by default.
- Setting the per-chute override causes capture to continue past activation.
- Shipments carry a `seq` marker; duplicates/replays are idempotent validator-side.
- `make lint-local sek8s` and `make test-local sek8s` pass; ≥90% coverage on new code.

---

## Constraints

- **No new Python dependency without AGENT.md sign-off.** Prefer shelling to the bundled `kubectl`
  via the `run_command` allowlist pattern; `kubernetes-asyncio` requires explicit approval.
- Async-first; no blocking calls in the capture loop.
- Config via `pydantic-settings` (env-driven), following `SystemManagerConfig`; no hardcoded URLs,
  paths, or credentials.
- Do **not** modify `setup_vm_tls` or anything in initramfs (keep RTMR2 stable).
- Bounded resource use: per-pod line/byte caps, max-capture-duration backstop, capped concurrency
  across pods.
- Hardened Deployment: non-root, read-only rootfs where possible, minimal mounts (SA token + the
  registry-tls leaf via hostPath), AppArmor annotation consistent with the attestation-proxy.

---

## Output Format

1. **Agent package** `src/sek8s/sek8s/log_shipper/`:
   - `config.py` — `LogShipperConfig(BaseSettings)`: validator base URL (the CVM mTLS host —
     `https://cvm.chutes.ai`), mTLS cert/key paths (`/run/chutes/registry-tls/...`), namespace
     (`chutes`), label selector (`chutes/chute=true`),
     batch size/flush interval, per-pod line/byte caps, max-capture-seconds, kubeconfig/SA token
     path, retry/backoff.
   - `agent.py` — watch chute pods (`kubectl get pods -w -o json` or client watch) → per new chute
     pod, spawn a capture task; read labels `chutes/config-id`, `chutes/chute-id`,
     `chutes/deployment-id`. Track pod phase for terminal detection.
   - `shipper.py` — per-pod: `kubectl logs -f --timestamps` (or client stream) → bounded batch →
     `POST /instances/launch_config/{config_id}/logs` over mTLS with `seq`, `server_ip` (the node
     IP), and pod metadata → key off the response's **status code** for the cutoff (a specific code
     = stop; success = keep sending) → stop on that / pod terminal / max-duration; ack-with-retry on
     transient failure. Optional light filtering of shim-excluded noise (`nvidia-smi`, `curl`, …).
   - Console entry in `src/sek8s/pyproject.toml` `[tool.poetry.scripts]`.
2. **RBAC + Deployment manifest** templated into `/var/lib/rancher/k3s/server/manifests/`
   (mode 0400 + immutable, mirroring the attestation-proxy delivery): `ServiceAccount
   chute-log-shipper` (ns `chutes`), `Role` (`pods` get/list/watch, `pods/log` get), `RoleBinding`,
   `Deployment` (1 replica, scoped SA, registry-tls leaf hostPath-mounted, hardened securityContext).
   The `server_ip` the agent ships comes from each watched pod's `status.hostIP` (accurate per pod,
   no downward API needed).
3. **Ansible role** `chute-log-shipper` (or fold into an existing manifest role): render the
   manifest + agent env, register in the playbook. Confirm the manifest path is in
   `tdx-measure-miner.conf`.
4. **Changelog fragment** in `changelogs/vm/unreleased/direct-boot.md` under `### Added`. No
   `VERSION` bump during development.

---

## Failure Conditions

- Reads pod logs via the purged admin kubeconfig instead of the scoped SA token.
- Runs as a DaemonSet / per-node, or grants RBAC beyond `pods`/`pods/log` in `chutes` (e.g.
  `secrets`, cluster-wide).
- Modifies `setup_vm_tls` / initramfs (shifts RTMR2 unnecessarily).
- Ships steady-state logs after the validator returns `stop` (ignores the cutoff), or has no
  max-duration backstop for a never-activating pod.
- Unbounded buffering / no per-pod caps (DRAM pressure).
- Drops logs silently on transient POST failure (must retry with backoff), or double-counts on
  retry (must send `seq` for idempotency).
- Bakes the validator URL, credentials, or the SA token into the image or source.
- Introduces a new Python dependency without AGENT.md sign-off.

---

## Rollout Notes

- **Measurement:** adds a measured RBAC manifest + Deployment + new Python image content → shifts
  **RTMR3**. RTMR2/initramfs untouched (reuses the existing registry mTLS leaf). Regenerate
  expected-measurement baselines in lockstep (`guest-tools/scripts/measurement/`) before rollout,
  or booted VMs fail attestation.
- **Ordering with the validator:** the agent is a no-op until the validator endpoints exist; ship
  the `chutes-api` side (or a stub returning `stop`) first, or gate the agent behind a config flag.
- **Phase 2 (separate change):** repoint the readiness/liveness probe off `:8001/_alive` → 8000
  **first** (`chutes-miner api/k8s/operator.py:_get_probe_port`), migrate `log_prober` and the
  `stream_logs`/`encrypted_logs` paths onto the agent/central store, extend the agent to ship
  running logs (default-off), then retire the 8001 server + NodePort and the `encrypted_logs` ECIES
  path. The job-output upload of `/tmp/_chute.log*` (`chutes/chute/job.py:236`) depends on the tee'd
  file, not the 8001 server, so it is unaffected.
- **Backward compat:** Phase 1 leaves the 8001 server and existing log proxies fully in place; the
  agent is purely additive.
