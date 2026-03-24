# Feature Spec: Docker Hub Credential Support for Cosign and Containerd

**Date**: 2026-03-24  
**Status**: draft

---

## Context

Cosign image signature verification and containerd image pulls both hit Docker Hub anonymously, sharing a single 100-pull/6-hour per-IP rate limit. At VM boot, the combination of containerd pulling workload images and cosign verifying signatures can exhaust this quota — especially after restarts during debugging or when the single-flight dedup fix (separate PR) is not yet in place. Once Docker Hub returns `TOOMANYREQUESTS`, cosign enters a 300-second global backoff and all admission is blocked.

Authenticated Docker Hub users get at least 200 pulls/6hrs (free tier) and significantly more on paid tiers. This feature adds optional Docker Hub credentials to the miner config pipeline so both containerd and cosign authenticate, avoiding the anonymous rate limit entirely.

The config volume (`/var/config`) is an **untrusted input boundary** — the miner provides the credentials on the host, and the guest must treat them as potentially adversarial. Credentials must never be interpolated into shell commands or subprocess arguments.

- **Packages affected**: `host-tools/scripts`, `ansible/k3s/roles/config`, `ansible/k3s/roles/common`, `ansible/k3s/roles/admission-controller`, `sek8s/cosign`
- **Key files**:
  - `host-tools/scripts/config/config.tmpl.yaml` — user-facing config template
  - `host-tools/scripts/config/config-schema.json` — JSON Schema for config validation
  - `host-tools/scripts/chutes_host/config.py` — YAML parser, emits shell vars
  - `host-tools/scripts/quick-launch.sh` — orchestrates VM launch, calls `create-config.sh`
  - `host-tools/scripts/volumes/create-config.sh` — creates and populates config volume
  - `ansible/k3s/roles/config/files/process-config.py` — guest-side config validator and applier
  - `ansible/k3s/roles/common/templates/registries.yaml.j2` — containerd registry config (k3s)
  - `sek8s/cosign/client.py` — cosign subprocess runner (`_COSIGN_ENV`)
  - `ansible/k3s/roles/admission-controller/files/admission-controller.service` — systemd unit for admission controller
- **Dependencies**: Depends on (but does not block) the single-flight cosign dedup fix. Both reduce Docker Hub rate limit pressure independently.

---

## Design Decisions

- **Credentials are optional**: If `docker_hub` is absent from config, the system operates exactly as it does today (anonymous pulls). No breaking change for existing miners.
- **File-only credential flow**: Credentials travel as files on the config volume, are validated in Python, and written to Docker `config.json` and `registries.yaml` files. They are **never** passed as subprocess arguments, shell expansions, or interpolated into executed strings.
- **Strict input validation at the guest boundary**: `process-config.py` validates credentials with strict regexes before using them. Username: `^[a-zA-Z0-9._-]{1,128}$`. Token: `^[a-zA-Z0-9_-]{1,512}$`. Anything else is rejected.
- **Fail open to anonymous**: If credentials are present but fail validation, skip Docker Hub auth and fall back to anonymous. Log a warning but do not halt boot. A miner with bad credentials is better off running anonymously than not booting.
- **Standard Docker auth format**: The Docker `config.json` uses the standard `{"auths": {"https://index.docker.io/v1/": {"auth": "<base64(user:token)>"}}}` format. Both cosign and Docker tooling natively consume this.
- **Two consumers, one source**: `process-config.py` generates auth for both containerd (`registries.yaml` inline auth) and cosign (`config.json` via `DOCKER_CONFIG`). The credentials are read once from the config volume and written to two destination formats.
- **Cosign gets a path, not credentials**: The admission controller service sets `DOCKER_CONFIG=/etc/admission-controller/docker-config` (a directory path). Cosign reads `config.json` from that directory. No credential values appear in environment variables, command lines, or process listings.

---

## API Changes

- **New endpoints**: None
- **Schema changes**: New optional `docker_hub` section in `config-schema.json` and `config.tmpl.yaml`
- **Migrations**: None (purely additive; existing configs without `docker_hub` continue to work)

---

## Goal

Success = A miner who provides Docker Hub credentials in their config.yaml has both containerd (k3s image pulls) and cosign (signature verification) authenticate to Docker Hub, avoiding the anonymous rate limit. Specifically:

1. A config.yaml with valid `docker_hub.username` and `docker_hub.token` results in authenticated Docker Hub pulls. Verify by checking `docker.io` rate limit headers show the authenticated tier.
2. A config.yaml without the `docker_hub` section boots normally with anonymous pulls (backward compatible).
3. A config.yaml with malformed credentials (special characters, excessive length, empty strings) falls back to anonymous and logs a warning — does not block boot.
4. Credentials never appear in `ps` output, journal logs, or any process argument list. Only file paths appear in env vars.
5. Credential files on disk have restrictive permissions (0600 root-only for config volume files, 0640 root:admission for the cosign Docker config).

---

## Constraints

- Credentials must never be interpolated into shell commands, subprocess arguments, or any context where special characters could escape process controls. Pure file I/O only.
- `process-config.py` must validate credentials with strict regexes before any use. Reject on first invalid character.
- The config volume is untrusted input. Treat all values read from `/var/config/` as adversarial.
- `create-config.sh` writes credential files using shell redirection to files only (`echo "$VAR" > file`), never passes them to external commands.
- The `DOCKER_CONFIG` env var contains a directory path, not credentials. Cosign and Docker tooling read `config.json` from that directory.
- No new Python dependencies. Use only stdlib (`base64`, `json`, `re`, `os`, `pathlib`).
- Must not break existing miners who have no `docker_hub` section in their config.

---

## Output Format

1. **Modified: `host-tools/scripts/config/config.tmpl.yaml`**
  - Add optional `docker_hub` section:
2. **Modified: `host-tools/scripts/config/config-schema.json`**
  - Add `docker_hub` to schema properties (not in `required`):
3. **Modified: `host-tools/scripts/chutes_host/config.py`**
  - Parse `docker_hub.username` and `docker_hub.token` from config.
  - Emit `DOCKER_HUB_USERNAME` and `DOCKER_HUB_TOKEN` shell vars via `shlex.quote` (defense in depth).
4. **Modified: `host-tools/scripts/quick-launch.sh`**
  - Accept `--docker-hub-username` / `--docker-hub-token` CLI overrides.
  - Pass credentials as additional positional args to `create-config.sh` (optional 8th and 9th args).
5. **Modified: `host-tools/scripts/volumes/create-config.sh`**
  - Accept optional Docker Hub username/token args.
  - Write `docker-hub-username` and `docker-hub-token` as files on the config volume (mode 0600).
  - No shell interpolation beyond writing to files.
6. **Modified: `ansible/k3s/roles/config/files/process-config.py`**
  - Read `/var/config/docker-hub-username` and `/var/config/docker-hub-token` (skip if missing).
  - Validate with `^[a-zA-Z0-9._-]{1,128}$` and `^[a-zA-Z0-9_-]{1,512}$` respectively.
  - If valid: generate `/etc/admission-controller/docker-config/config.json` with standard Docker auth (base64-encoded `username:token`), mode 0640 root:admission.
  - If valid: append Docker Hub auth to `/etc/rancher/k3s/registries.yaml` (containerd config) — add `docker.io` mirror with inline `username`/`password` under `configs`.
  - If invalid: log warning, skip auth setup, continue boot.
7. **Modified: `ansible/k3s/roles/common/templates/registries.yaml.j2`**
  - Add commented Docker Hub mirror block with placeholder for runtime injection:
8. **Modified: `sek8s/cosign/client.py`**
  - In `_COSIGN_ENV` construction: if `/etc/admission-controller/docker-config` exists, set `DOCKER_CONFIG` to that path. Otherwise omit (anonymous).
9. **Modified: `ansible/k3s/roles/admission-controller/files/admission-controller.service`**
  - Add `Environment=DOCKER_CONFIG=/etc/admission-controller/docker-config` to `[Service]` section.
10. **Modified: `ansible/k3s/roles/admission-controller/tasks/configure-cosign.yml`**
  - Ensure `/etc/admission-controller/docker-config` directory exists at build time (mode 0750, root:admission).

---

## Failure Conditions

- Credentials are passed as subprocess arguments or shell expansions anywhere in the pipeline.
- A username or token containing shell metacharacters (`;`, `|`, `$`, ```, `\n`, etc.) causes command injection or unexpected behavior.
- Malformed credentials halt boot instead of falling back to anonymous.
- Credential values appear in journal logs, `ps` output, or `/proc/*/cmdline`.
- Existing miners without `docker_hub` in their config fail to boot (backward compatibility regression).
- `process-config.py` writes credentials without validating them first.
- Docker `config.json` has world-readable permissions.
- containerd `registries.yaml` auth section has world-readable permissions.
- The `DOCKER_CONFIG` environment variable contains actual credentials instead of a file path.

---

## Rollout Notes

- **Backward compatible**: No changes needed for existing miners. `docker_hub` section is optional in both schema and all code paths.
- **Miner action required for auth**: Miners who want authenticated pulls must add `docker_hub.username` and `docker_hub.token` to their `config.yaml` and re-create the config volume (or re-run `quick-launch.sh` which recreates it).
- **Existing config volumes**: Config volumes created before this feature simply lack the `docker-hub-username`/`docker-hub-token` files. `process-config.py` skips auth setup when these files are missing.
- **Token type**: Docker Hub Personal Access Tokens (PATs) with `Read-only` scope are sufficient. Miners should not use their password. The config template and help text should guide them to create a read-only PAT.
- **Rate limit tiers**: Free Docker Hub accounts get 200 pulls/6hrs authenticated (vs 100 anonymous). Paid Docker Pro/Team/Business accounts get higher limits. The feature works with any valid Docker Hub credential.
- **Interaction with single-flight dedup**: The single-flight fix (separate PR) reduces Docker Hub calls from `N * images` to `1 * images` on cold start. Docker Hub credentials increase the budget. Together they make rate limiting effectively impossible under normal operation.
- **Config volume re-creation**: The config volume is always re-created by `quick-launch.sh` unless the user provides a pre-existing `--config-volume` path. On a normal launch, credentials are always fresh from the current config.yaml.

