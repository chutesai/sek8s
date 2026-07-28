# Feature Spec: Chute Log Shipper (guest side)

**Date**: 2026-07-23
**Status**: draft
**Revised**: 2026-07-25 (architecture finalized — standalone VM service reading CRI + `/var/log/pods`)
**Revised**: 2026-07-27 (wire contract finalized — log-lines-only body; identity resolved server-side
from the mTLS cert + path + proxy; `204` = stop; `seq`/`server_ip`/pod-metadata dropped)

---

## Context

When a chute crashes or errors **before its instance is registered in the validator**, its logs
are unreachable. Every current log path needs the validator to know the chute's `host:port`, which
only exists once an `Instance` row is created — on launch-config *claim*, after verification. A
chute that dies before claim leaves a `LaunchConfig` with `failed_at` and **no Instance**, so the
miner CLI and the validator's `encrypted_logs` capture both have nothing to read. And because a
launch config is not tied to a server, the validator cannot even know which node to look at — only
an **in-guest** component (with local visibility into the k3s node) can see the pod in time.

This spec covers the **guest-side agent** that closes that gap: a single standalone systemd service
in the attested guest image that discovers chute pods locally, reads their logs off disk, and
streams them outbound to the validator, which caches them and controls when capture stops. The
validator side is specified separately in
[chute-log-shipper-validator-api.md](chute-log-shipper-validator-api.md) (portable, to be
implemented in `chutes-api`).

Delivered in two phases so the gap fix ships without depending on the riskier retirement of the
per-chute 8001 log server (Phase 2).

- **Packages affected**: `src/sek8s/` (new agent package), `ansible/guest/`
- **Key files**:
  - `src/sek8s/sek8s/log_shipper/` (new — agent package: `config.py`, `agent.py`, `shipper.py`,
    console entry in `src/sek8s/pyproject.toml`), mirroring the `system_manager` layout
  - `ansible/guest/roles/chute-log-shipper/` (new — systemd unit + restricted crictl wrapper + env +
    AppArmor profile + boot privilege wiring), mirroring the **`system-manager`** role
  - `ansible/guest/roles/apparmor-hardening/files/profiles/sek8s.chute-log-shipper` (new — confine
    the service to the specific log paths, the crictl socket, the registry-tls leaf, and egress)
  - `ansible/guest/playbooks/chutes-miner-vm.yml` (register the new role)
  - `changelogs/vm/unreleased/direct-boot.md` (`### Added` fragment)
- **Reused, unchanged**:
  - `/run/chutes/registry-tls/client.{crt,key}` — the per-boot registry mTLS leaf minted by
    `ansible/guest/roles/vm-tls/files/initramfs/setup_vm_tls` (egress credential; **no change** to
    `setup_vm_tls`, so RTMR2/initramfs is untouched). This leaf is the *only* identity the agent
    needs — the validator resolves `(miner_hotkey, vm_name)` from it server-side, so the guest never
    has to know or send its own `vm_name`.
  - `ansible/guest/roles/system-manager/` — reference for the **standalone systemd service**
    pattern: `.service` unit, `.env.j2` config, restricted `k3s ctr` wrapper
    (`k3s-images-helper`), and the `run_command` `create_subprocess_exec` allowlist
  - `src/sek8s/sek8s/system_manager/status/util.py` (`run_command`) — safe `create_subprocess_exec`
    allowlist pattern for shelling to the bundled `k3s crictl`
- **Dependencies / external** (out of scope here, required for the feature to function):
  - `chutes-api`: the ingest/cutoff/storage/read endpoints in the validator spec
  - `chutes-miner-cli`: `instance-logs` fallback to the cached logs (validator spec §CLI)

---

## Design Decisions

- **Standalone VM systemd service, not an in-cluster workload.** Every other sek8s Python service in
  the guest (`system-manager`, `system-status`, `attestation-service`, `admission-controller`) runs
  as a **systemd service on the VM**; only the lean `attestation-proxy` runs in-cluster. The log
  shipper follows the dominant pattern: it runs on the VM with native filesystem access (trivial
  cursor persistence, direct reads of the registry-tls leaf), outside the very cluster it observes.
- **No k8s API access at all — read CRI + log files off disk.** The admin kubeconfig
  (`/etc/rancher/k3s/k3s.yaml`) is purged at boot, and a non-pod process gets no projected
  ServiceAccount token. Rather than mint and rotate a standalone credential, the service:
  - **discovers pods + reads their labels/uid/phase** by shelling to the bundled
    **`k3s crictl pods -o json`** through a restricted wrapper (the CRI sandbox metadata carries the
    pod's k8s labels and UID — `k3s ctr` does **not** expose k8s pod labels). The agent consumes only
    `chutes/config-id` (→ request path) and the pod UID (→ log path); the `chutes/chute-id` /
    `chutes/deployment-id` labels are present but unused, since the validator derives all identity
    server-side, and
  - **reads log content** directly from `/var/log/pods/chutes_<pod>_<uid>/<container>/*.log` (CRI
    format, `<RFC3339Nano> stdout F <line>`; rotation is handled by kubelet).

  This removes the entire ServiceAccount / RBAC-manifest / projected-token surface the earlier draft
  carried, and adds **no new Python dependency** (reuses the `run_command` shell pattern +
  already-present `aiohttp`). The authorization ceiling becomes a **kernel-LSM (AppArmor)** boundary
  over specific paths + the crictl socket, not an API-server RBAC ceiling.
- **Minimal request body — identity resolved server-side, not self-asserted.** The shipment body
  carries only the log lines (`{"logs": [{ts, stream, log}]}`); everything else the validator
  derives, because a value the guest could assert about itself is strictly less trustworthy than one
  the infrastructure observes:
  - `config_id` ← the request path.
  - `(miner_hotkey, vm_name)` — and therefore the server — ← the presented registry-tls leaf,
    verified against the registered per-boot VM CA. `vm_name` is unique *per* `miner_hotkey`, and the
    leaf binds to a specific server registered to a specific miner, so the cert is the durable
    identity anchor. The guest sends **neither** `miner_hotkey` nor `vm_name`.
  - `server_ip` is **not** sent: the CVM mTLS proxy (`cvm.chutes.ai` nginx) observes the source IP
    and passes it to the API. It is also not a durable key — currently unique per server, but not
    guaranteed to stay so — whereas the cert → `(miner, vm)` → server mapping is.
- **Push, with validator-controlled cutoff (simple).** The agent streams batches outbound and keys
  off the response **status code** to decide whether to keep sending or stop: **`204 No Content` =
  stop**, any other 2xx = keep sending, and a non-2xx or connection error = transient failure →
  retry with backoff (cursor unchanged). All cutoff *logic* lives in the API (default: stop at
  activation; per-chute override keeps it going). The agent holds no cutoff policy of its own — it
  obeys the code — plus a local max-duration backstop so a never-activating, never-terminating pod
  cannot stream forever.
- **Egress auth = mTLS, enforced.** Reuse the per-boot registry mTLS leaf (no new leaf, no
  `setup_vm_tls` edit → no RTMR2 shift). The validator verifies the presented leaf against the
  registered per-boot VM CA → `(miner_hotkey, vm_name)`, binding each shipment to the attested boot.
  There is no non-mTLS path.
- **Timestamp-based resume/dedupe, not line-index.** Logs are read with CRI RFC3339-nanosecond
  timestamps. The validator dedupes on `(config_id, ts)` (nanosecond ts is effectively unique per
  line in a single container stream). **No `seq`/gap marker is sent:** batches retry on failure and
  the cursor advances only on a successful ship, so no batch is ever silently dropped for a `seq` to
  detect — the reliable-delivery design already provides the guarantee `seq` was meant to hint at.
  Timestamp-keyed dedupe survives kubelet log rotation (line indices are not stable across rotation)
  and agent restarts. The service persists a cursor file
  `{config_id → last_shipped_ts}` and, on restart, reconciles it against the live pod set (dropping
  keys for pods no longer present to prevent unbounded growth; entries are also evicted on
  pod-delete while running).

---

## API Changes

The guest agent is a **client**; it calls the validator. No inbound API is added on the guest.
- **Calls out**: the log-ship endpoint (validator) presenting the mTLS leaf. **Must target the
  dedicated CVM mTLS host — `cvm.chutes.ai`**, a **separate proxy** that fronts *all* CVM mTLS
  endpoints (a vendor-neutral consolidation of what `tdx-attestation.chutes.ai` used to serve
  piecemeal), **not** plain `api.chutes.ai`, which does not verify client certs. The legacy
  `tdx-attestation.chutes.ai` remains only for already-booted old VMs (see validator spec §2
  backward-compat).
- **Path — `POST /instances/launch_config/{config_id}/logs`** (the validator spec's §1), on the
  `cvm.chutes.ai` mTLS host. `config_id` comes from the pod label; there is no `vm_name` in the path
  — the mTLS leaf resolves the VM identity server-side.
- **Request body — log lines only:** `{"logs": [{"ts": "<RFC3339Nano>", "stream": "stdout|stderr",
  "log": "<line>"}]}`. The guest self-asserts no identity or metadata: `config_id` ← path,
  `(miner_hotkey, vm_name)` + server ← the verified mTLS leaf, source IP ← the proxy. No `seq`,
  `server_ip`, `miner_hotkey`, `vm_name`, `chute_id`, `deployment_id`, `pod_uid`, or `container` is
  sent — each is derivable server-side or redundant.
- **Response contract — cutoff by status:** `204 No Content` = stop capturing this pod; any other
  2xx = keep sending; non-2xx or a connection error = transient → retry with backoff.
- **Proxy enforcement is API-side and transparent to the guest.** The dedicated CVM proxy terminates
  mTLS and injects a **secret header** that the API validates, so mTLS-required routes can only be
  reached through the proxy (defense in depth against a request bypassing the proxy on the internal
  network). **The guest neither sends nor knows this secret** — it only presents the mTLS leaf; the
  proxy adds the header. So the guest contract is unchanged by this mechanism: ship to
  `https://cvm.chutes.ai/instances/launch_config/{config_id}/logs` with the client cert. All routing
  and header-secret handling live entirely on the validator/proxy side.
- **Schema changes**: none in this repo.
- **Migrations**: none.

---

## Goal

Success (Phase 1) =
- The agent runs as a single `chute-log-shipper.service` on the VM, as a dedicated non-root uid,
  confined by an AppArmor profile that permits only `/var/log/pods/chutes_*/**` reads, the crictl
  socket, the registry-tls leaf, the cursor dir, and egress to the validator.
- `k3s crictl pods -o json` (via the restricted wrapper) yields chute pods with their
  `config-id` label + uid + phase; the service joins uid →
  `/var/log/pods/chutes_<pod>_<uid>/…` and reads the log files.
- For a chute that **crashes during warmup and never registers an instance**, its logs are shipped
  to the validator and become retrievable (via `chutes-miner instance-logs` and the support view).
- For a chute that **activates normally**, the validator returns `204` and the agent ceases
  capture for that pod — no steady-state logs are shipped by default.
- Setting the per-chute override causes capture to continue past activation.
- Shipments carry RFC3339-nanosecond per-line `ts`; duplicates/replays are idempotent
  validator-side on `(config_id, ts)`. No `seq` is sent — reliable delivery makes it redundant.
- `make lint-local sek8s` and `make test-local sek8s` pass; ≥90% coverage on new code.

---

## Constraints

- **No new Python dependency.** Shell to the bundled `k3s crictl` via a restricted wrapper +
  `run_command` allowlist; egress uses the already-present `aiohttp`. (No `kubernetes-asyncio`: the
  service does not touch the k8s API.)
- Async-first; no blocking calls in the capture loop.
- Config via `pydantic-settings` (env-driven), following `SystemManagerConfig`; no hardcoded URLs,
  paths, or credentials.
- Do **not** modify `setup_vm_tls` or anything in initramfs (keep RTMR2 stable).
- Bounded resource use: per-pod line/byte caps, max-capture-duration backstop, capped concurrency
  across pods, bounded cursor file (reconciled to the live pod set).
- Hardened service: dedicated non-root uid (not 1000, per the system-manager isolation rule),
  least-privilege group/ACL wiring at boot for the crictl socket + `/var/log/pods` + the registry-tls
  leaf, and an AppArmor profile consistent with `sek8s.system-manager`.

---

## Output Format

1. **Agent package** `src/sek8s/sek8s/log_shipper/`:
   - `config.py` — `LogShipperConfig(BaseSettings)`: validator base URL (the CVM mTLS host —
     `https://cvm.chutes.ai`), mTLS cert/key paths (`/run/chutes/registry-tls/...`), namespace
     (`chutes`), label selector (`chutes/chute=true`), crictl wrapper path, `/var/log/pods` root,
     cursor file path, poll interval, batch size/flush interval, per-pod line/byte caps,
     max-capture-seconds, retry/backoff.
   - `agent.py` — poll `k3s crictl pods -o json` (via the restricted wrapper) on an interval →
     filter to chute pods (label selector) → per new chute pod, spawn a capture task; read the
     `chutes/config-id` label (→ request path), the pod uid (→ log path), and phase (for terminal
     detection). Reconcile the cursor file against the live pod set. (No `chute-id`/`deployment-id`
     labels or node IP are read — the validator derives all identity server-side, so the agent only
     needs `config-id` + uid.)
   - `shipper.py` — per-pod: tail `/var/log/pods/chutes_<pod>_<uid>/<container>/*.log`, parse the CRI
     line format (`<RFC3339Nano> <stdout|stderr> <F|P> <msg>`), resume from the cursor `last_ts` →
     bounded batch → `POST /instances/launch_config/{config_id}/logs` over mTLS with a log-lines-only
     body (`{"logs": [{ts, stream, log}]}`; no `seq`/`server_ip`/metadata — all derived server-side
     from the path + mTLS leaf + proxy) → key off the response **status code** for the cutoff (`204`
     = stop; any other 2xx = keep sending) → stop on `204` / pod terminal / max-duration;
     retry-with-backoff on non-2xx or connection error (cursor unchanged); advance the cursor on
     successful ship. Optional light filtering of shim-excluded noise (`nvidia-smi`, `curl`, …).
   - Console entry `chute-log-shipper` in `src/sek8s/pyproject.toml` `[tool.poetry.scripts]`.
2. **Ansible role** `chute-log-shipper` (mirroring `system-manager`):
   - `chute-log-shipper.service` systemd unit (`User=` dedicated non-root uid, `After=` k3s, restart
     policy), `chute-log-shipper.env.j2` rendered config, a restricted `k3s crictl` wrapper (allow
     only `pods -o json` / `ps` read verbs, à la `k3s-images-helper`), boot tasks that create the
     uid, wire group/ACL read on the crictl socket + `/var/log/pods` + the registry-tls leaf, and
     create the cursor dir owned by that uid.
   - `sek8s.chute-log-shipper` AppArmor profile (deliver via `apparmor-hardening`): read
     `/var/log/pods/chutes_*/**`, the crictl socket, and the registry-tls leaf; read/write the cursor
     dir; network egress to the validator only; deny the rest.
   - Register the role in `ansible/guest/playbooks/chutes-miner-vm.yml`.
3. **Changelog fragment** in `changelogs/vm/unreleased/direct-boot.md` under `### Added`. No
   `VERSION` bump during development.

---

## Failure Conditions

- Reads pod logs via the purged admin kubeconfig, or introduces a k8s API credential / SA token at
  all (the design is deliberately credential-free — CRI socket + log files only).
- Runs as an in-cluster Deployment / DaemonSet, or grants the service more filesystem/socket reach
  than the specific log paths + crictl socket + registry-tls leaf (AppArmor must confine it).
- Runs as root or as uid 1000 (must be a dedicated non-root uid, per the system-manager isolation
  rule).
- Modifies `setup_vm_tls` / initramfs (shifts RTMR2 unnecessarily).
- Ships steady-state logs after the validator returns `204` (ignores the cutoff), or has no
  max-duration backstop for a never-activating pod.
- Self-asserts identity in the request body (`miner_hotkey`, `vm_name`, `server_ip`, or pod
  metadata) instead of letting the validator derive it from the path + mTLS leaf + proxy.
- Uses a line-index dedupe key (breaks across kubelet log rotation / restarts) instead of the CRI
  `ts`; or ships without per-line `ts`.
- Lets the cursor file grow unbounded (must reconcile to the live pod set).
- Unbounded buffering / no per-pod caps (DRAM pressure).
- Drops logs silently on transient POST failure (must retry with backoff), or double-counts on
  retry (validator dedupes on `(config_id, ts)`; send `ts`).
- Bakes the validator URL or credentials into the image or source (all env/file-driven).
- Introduces a new Python dependency without AGENT.md sign-off.

---

## Rollout Notes

- **Measurement:** adds new Python image content (`log_shipper` package) + a systemd unit + a
  restricted crictl wrapper + an AppArmor profile → shifts **RTMR3**. RTMR2/initramfs untouched
  (reuses the existing registry mTLS leaf; no initramfs edit). No RBAC/auto-deploy manifest is added
  (the service is not in-cluster). Regenerate expected-measurement baselines in lockstep
  (`guest-tools/scripts/measurement/`) before rollout, or booted VMs fail attestation.
- **Ordering with the validator:** the agent is a no-op until the validator endpoints exist; ship
  the `chutes-api` side (or a stub returning `204`) first, or gate the agent behind a config flag.
  A shipment to a missing endpoint must fail closed (retry/backoff, no crash).
- **Validator-side deltas to confirm** (in the still-draft companion spec, easy to adjust):
  1. Route the ingest endpoint (`/instances/launch_config/{config_id}/logs`, §1) through the new
     **dedicated `cvm.chutes.ai` CVM mTLS proxy** and gate it on that proxy's injected secret header
     (the API-side enforcement described in §API Changes). No change to the guest, which just presents
     the mTLS leaf.
  2. Dedupe on **`(config_id, ts)`** (nanosecond) so agent restarts and kubelet log rotation stay
     idempotent. No `seq` is sent — reliable delivery (retry + advance-cursor-on-success) makes it
     redundant.
  3. Resolve identity server-side: `config_id` from the path, `(miner_hotkey, vm_name)` + server from
     the verified mTLS leaf, source IP from the proxy. The guest self-asserts none of these; the
     request body is log lines only (`{"logs": [{ts, stream, log}]}`).
  4. Return **`204 No Content`** to signal cutoff (any other 2xx keeps capture going).
- **Phase 2 (separate change):** repoint the readiness/liveness probe off `:8001/_alive` → 8000
  **first** (`chutes-miner api/k8s/operator.py:_get_probe_port`), migrate `log_prober` and the
  `stream_logs`/`encrypted_logs` paths onto the agent/central store, extend the agent to ship
  running logs (default-off), then retire the 8001 server + NodePort and the `encrypted_logs` ECIES
  path. The job-output upload of `/tmp/_chute.log*` (`chutes/chute/job.py:236`) depends on the tee'd
  file, not the 8001 server, so it is unaffected.
- **Backward compat:** Phase 1 leaves the 8001 server and existing log proxies fully in place; the
  agent is purely additive.
