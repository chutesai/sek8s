# System Status Service

## Purpose
The System Status service is a read-only FastAPI endpoint that runs inside the guest VM to expose operational state from a tightly scoped set of systemd units and NVIDIA GPU telemetry commands. It is intended to de-risk the "black-box" nature of the VM by providing authenticated components (admission controller, attestation proxy, etc.) with structured status information without granting shell access or generic command execution capabilities.

## Functional Scope

| Capability | Description |
| --- | --- |
| Service inventory | Enumerate the fixed allowlist of managed systemd units (admission controller, attestation service, k3s server). |
| Service status | Return summarized health derived from `systemctl show` for an allowlisted unit. |
| Service logs | Tail the latest N log lines (`journalctl -u <unit>`) with optional time window filtering. |
| GPU telemetry | Surface `nvidia-smi` output in either default (summary) or `-q` (detailed) modes with optional GPU index selection. |

Future enhancements (e.g., additional units) must be added explicitly to the allowlist to avoid broadening the attack surface.

## API Surface

All responses are JSON and delivered over HTTPS or a Unix Domain Socket based on standard `ServerConfig` parameters.

- `GET /health`
  - Returns `{"status": "ok"}` when the service is responsive.
- `GET /services`
  - Lists the static allowlist: service id, systemd unit name, description.
- `GET /services/{service_id}/status`
  - Summarizes `LoadState`, `ActiveState`, `SubState`, `MainPID`, and recent exit code harvested from `systemctl show`.
- `GET /services/{service_id}/logs?lines=200&since_minutes=60`
  - Streams log lines from `journalctl -u <unit>`.
  - `lines` defaults to 200 and is clamped to [1, 1000].
  - `since_minutes` (optional) truncates the log window to the last N minutes (1–1440). When omitted, only the latest `lines` are returned.
- `GET /gpu/nvidia-smi?detail=false&gpu=all`
  - Executes `nvidia-smi`.
  - `detail=true` swaps the command to `nvidia-smi -q`.
  - `gpu` can be `all` (default) or an integer GPU index; only a single index is accepted to keep the interface deterministic.
  - Output is returned as `{ "stdout": "...", "stderr": "...", "exit_code": <int> }`.

All other paths return 404.

## Security Model

1. **Read-only execution**
   - Only `systemctl show`, `journalctl -u`, and `nvidia-smi` commands are ever issued. Parameterization is handled server-side through validated inputs (service ids, bounded integers, boolean flags).
   - `subprocess` calls are made with `shell=False`, preventing shell interpolation or arbitrary redirection.
   - Each command has a strict timeout (default 10 seconds) and the stdout/stderr is size-limited before returning to the caller.

2. **Allowlist enforcement**
   - Service ids are resolved against a hard-coded dictionary mapping to systemd unit names (`admission-controller.service`, `attestation-service.service`, `k3s.service`). Requests for unknown ids fail with HTTP 404.
   - GPU command options are derived from boolean and integer query parameters; textual arguments are never concatenated into the command line.

3. **Principle of least privilege**
   - The systemd unit runs as a dedicated `status` user (or another non-privileged account) with membership in the `systemd-journal` and `video` groups. It does not require root and is fully confined via a drop-in (`ProtectSystem=strict`, `NoNewPrivileges=true`, etc.).
   - Application directories live under `/opt/sek8s` with read-only permissions for service users.

4. **Transport security**
   - The service reuses the existing `ServerConfig` foundation: TLS is mandatory for TCP bindings, and UDS deployments (default) inherit filesystem ACLs.

5. **Operational safeguards**
   - Log and command outputs are truncated (configurable, default 16 KiB) to minimize potential sensitive data exposure.
   - Errors returned to clients omit raw stderr to avoid leaking host paths or kernel details; instead a structured error payload describes the failure mode (timeout, exit code, etc.).

## Open Questions / Next Steps

- Determine the final authentication story (e.g., reuse validator signature headers similar to the attestation proxy or rely on mTLS). The initial implementation focuses on the read-only execution layer; transport-level protections can be layered in once the consuming component is chosen.
- Extend the allowlist if additional services (OPA, attestation proxy) need coverage.
- Consider Prometheus metrics (command success/failure counts) if observability gaps appear.
