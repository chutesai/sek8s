# Feature Spec: Standalone VM (Executor)

**Date**: 2026-03-17
**Status**: in progress

---

## Context

- **Packages affected**: `ansible/guest/`, `ansible/host/` (new), `host-tools/`, `guest-tools/`, `sek8s/services/`, `sek8s/system_manager/`
- **Key files**:
  - `ansible/guest/playbooks/site.yml` -- existing TEE playbook, to be renamed `tee-gpu-vm.yml`
  - `ansible/guest/roles/` -- all shared guest VM roles (12 general-purpose, 5 TEE-specific)
  - `host-tools/scripts/quick-launch.sh` -- VM launch orchestration
  - `host-tools/scripts/chutes_host/passthrough.py` -- multi-GPU passthrough
  - `host-tools/scripts/config/config.tmpl.yaml` -- config template (used by cloud-init for iPXE)
  - `ansible/guest/roles/chutes-gpu/` -- chart installation role (needs executor chart variant)
  - `ansible/guest/roles/config/files/process-config.py` -- config volume processing
  - `ansible/guest/roles/system-manager/` -- system-manager role (group rename: `tdx` -> descriptive name)
  - `ansible/guest/roles/cache-volume/` -- cache volume role (references `tdx` group GID 1000)
  - `sek8s/system_manager/cache/manager.py` -- cache manager (references `tdx` group in docstrings/logic)
  - `tdx/setup-tdx-host.sh` -- TDX host setup (wrapped by host provisioning playbook)
  - `tdx/attestation/setup-attestation-host.sh` -- installs PCCS, QGSD, QPL packages
  - `sek8s/services/attestation_proxy.py` -- TEE-only service, excluded from non-TEE executor
  - `sek8s/services/util.py` -- validator SR25519 signature auth (`authorize()`, `verify_validator_signature()`) -- reused by host update service
- **Dependencies**: Companion changes in chutes-miner (chutes-executor chart with gepetto-lite, confirmed available before this work begins), optional chutes-api `cluster_name` parameter

---

## Design Decisions

1. **Same repo, separate playbooks**: Rename `ansible/k3s/` to `ansible/guest/` (pairs with new `ansible/host/`, accurately describes guest VM image build). Create `executor-vm.yml` (non-TEE) and `tee-executor-vm.yml` (TEE) alongside the renamed `tee-gpu-vm.yml` (was `site.yml`). All three share the same role library under `ansible/guest/roles/`. No TEE/non-TEE conditionals inside roles -- separation lives entirely at the playbook level. Chart naming across the ecosystem: `chutes-control` (central control plane, was `chutes-miner`), `chutes-gpu` (GPU workers, was `chutes-miner-gpu`), `chutes-executor` (standalone self-scheduling, new).

2. **Executor chart role**: New Ansible role `chutes-executor` (or parameterize the existing `chutes-gpu` role with a `chart_name` variable) that installs the `chutes-executor` Helm chart (Redis + gepetto-lite + registry) instead of `chutes-gpu`. The playbook determines which role/variable to use.

3. **TEE-specific roles are LUKS and attestation-service only**: `prime-vm` and `harden-access` are included in all playbooks (not just TEE). `prime-vm` stabilizes the image for consistent checksums across boots (`prepare-vm-image.sh` SHA256 verification would fail otherwise). `harden-access` (disable SSH/getty/sudo) is defense-in-depth even on non-TEE -- raises the bar for tampering with a running VM. Both are still skipped when `debug_build` / `enable_dev_mode` are set, preserving the dev workflow.

4. **Keep system-manager for all variants**: Provides status, cache, and image management APIs. Requires group rename cleanup: `tdx` (GID 1000) is actually used for cache/pod data access (not TDX-specific) and should be renamed to something like `chutes-data`. `tdx-attest` (GID 987) is legitimately TEE-specific (attestation TLS sharing) and only needed in TEE variants. The system-manager service currently runs as `Group=tdx` which is misleading. This rename affects: `ansible/guest/roles/system-manager/`, `ansible/guest/roles/cache-volume/`, `sek8s/system_manager/cache/manager.py`, and any Helm chart values that set `runAsGroup: 1000`.

5. **Attestation-proxy only on TEE**: Requires TDX quotes and NVIDIA GPU evidence. Excluded from `executor-vm.yml`, included in `tee-executor-vm.yml` and `tee-gpu-vm.yml`.

6. **Config volume shared across all paths**: hostname, miner-ss58, miner-seed, network-config. Non-TEE skips LUKS-gated boot -- config volume mounts directly (no initramfs attestation unlock). New optional fields for port configuration (agent_port, attestation_port, node_port_range).

7. **Dual deployment paths, VM architecture preserved**: The qcow2 is the canonical build artifact. Individual miners deploy it via `quick-launch.sh` (QEMU/KVM with GPU passthrough, config volume, bridge networking -- same as today). For data centers, a host provisioning Ansible playbook automates the full host setup and is the basis for an iPXE-bootable host image.

8. **Host provisioning as Ansible playbook**: New playbook directory `ansible/host/` that automates the currently-manual host setup process: TDX kernel/firmware setup, PCCS auto-configuration, QEMU/KVM installation, host-tools deployment, guest qcow2 placement. Two host variants:
    - **TEE host** (`ansible/host/playbooks/tee-host.yml`): TDX setup (wraps `setup-tdx-host.sh`), PCCS package installation, QEMU/KVM, host-tools, guest qcow2.
    - **Non-TEE host** (`ansible/host/playbooks/host.yml`): QEMU/KVM, host-tools, guest qcow2. No TDX, no PCCS.
    
    Both host variants install required host-level dependencies (e.g., `aria2c` for VM image downloads used by `quick-launch.sh`, Python + PyYAML for orchestration scripts).
    
    For iPXE: the playbook output is baked into a host OS image. Cloud-init delivers a complete `config.yaml` (the same format as `config.tmpl.yaml`) per machine -- the DC provisioning server templates it with all machine-specific values: miner credentials, network (NIC name, IPs, DNS), volume paths and sizes (based on the machine's storage topology), port assignments, and Intel PCCS API key (TEE hosts). A first-boot systemd service writes the cloud-init-provided config to disk and runs `quick-launch.sh` with it. No auto-detection of any kind -- the DC provisioning server is the source of truth for all per-machine configuration.

9. **PCCS auto-configuration via cloud-init**: Currently `pccs-configure` is interactive and `PCKIDRetrievalTool` is manual (host README step 2). For automated/iPXE deployments, the Intel PCCS API key is delivered via cloud-init user-data alongside miner credentials (hostname, miner-ss58, miner-seed). The host provisioning playbook pre-installs PCCS packages; at deploy time, a first-boot service reads the API key from cloud-init, runs non-interactive PCCS configuration, and completes `PCKIDRetrievalTool` registration. Without PCCS properly configured, QGSD cannot fetch PCK certificates and TDX attestation fails at VM boot. TEE-host-only; non-TEE hosts skip PCCS entirely.

10. **First-boot port configuration with strict ordering**: Ports aren't known at image build time. A first-boot init script reads ports from the config volume and runs `helm upgrade` on the pre-installed chutes-executor chart. Critical dependency chain that must be enforced:
    1. k3s API must be up and healthy
    2. Network connectivity must be established (netplan applied from config volume)
    3. `helm upgrade` sets correct NodePort values on chutes-executor services
    4. Helm upgrade must trigger pod restarts so services bind to the new ports
    5. Services must be confirmed running on the correct ports **before** gepetto-lite self-registration starts
    
    If registration races ahead of port configuration, it advertises wrong ports to the validator. The `k3s-cluster-init` service (which already waits for k3s API) is the right place for this, but the executor port init script must run before any registration init script, and must verify the upgraded services are healthy before proceeding. Needs confirmation that `helm upgrade` with changed NodePort values actually restarts the affected pods (or whether an explicit rollout restart is needed).

11. **Host update service for remote VM lifecycle management**: A lightweight FastAPI service running on the host (not inside the guest VM) that allows the validator to remotely trigger updates. Reuses the existing validator SR25519 signature authentication from `sek8s/services/util.py` (`authorize(allow_validator=True)` + `ALLOWED_VALIDATORS`). Only requests signed by an allowed validator are accepted. On receiving an update command, the service:
    1. Pulls the latest `main` branch of the sek8s repo (updated launch scripts, host-tools)
    2. Checks for a new VM image (compares SHA256 of remote image against current)
    3. If new image available: downloads it from R2/CDN (atomic -- old VM keeps running until download completes and verifies)
    4. Gracefully shuts down the running VM by calling the system-manager `/shutdown` endpoint inside the VM, signed with the miner credentials (ss58 + seed from the cloud-init config on the host). This allows k3s to drain workloads and pods to terminate cleanly.
    5. Waits for the VM to power off (with timeout, falls back to QEMU process kill if shutdown hangs)
    6. Relaunches with `quick-launch.sh` using the existing `config.yaml`. No `--clean` needed -- bridge, TAP, iptables rules, and config volume persist across VM restarts and don't change between updates.
    
    If the download fails, the old VM continues running. The service reports update status (idle, downloading, restarting, success, failed) so the validator can track progress. Installed by the host provisioning playbook as a systemd service, exposed on a configurable port on the host's public interface (not a k3s NodePort -- this runs directly on the host, outside the VM).

12. **Future: native bare metal image**: A longer-term goal is a separate image type that runs directly on bare metal with native GPU drivers, no VM/hypervisor overhead. This requires different Ansible roles for GPU, networking, boot, and config delivery. Out of scope for this spec but noted as the ideal end state for data center deployments.

---

## API Changes

- **No new API endpoints in sek8s**: Registration API lives in chutes-miner. sek8s provides guest-side fail-fast logic (already exists in system-manager/status).
- **Config volume schema extension**: Add optional fields: `agent_port`, `attestation_port`, `node_port_range_start`, `node_port_range_end`, `tee_mode` (bool). `process-config.py` writes these to a location readable by first-boot scripts.
- **k3s-cluster-init extension**: New init script `04-k3s-executor-ports.sh` reads port config from `/var/config` and runs `helm upgrade` on chutes-executor with correct NodePort values. Must execute and confirm service health before registration begins.
- **Node label changes**: `02-k3s-label.sh` sets `chutes/tee=true|false` based on `tee_mode` field from config volume.
- **No schema migrations**: No Postgres in executor. Port fields on the server record are a chutes-api concern.

---

## Goal

Success criteria:

1. Three VM images can be built from the same role library using different playbooks: `tee-gpu-vm.yml`, `tee-executor-vm.yml`, `executor-vm.yml`
2. The non-TEE executor boots on bare metal without TDX hardware, includes k3s + GPU drivers + chutes-executor chart
3. The TEE executor boots on TDX hosts with LUKS + attestation + chutes-executor chart
4. Multi-GPU passthrough works identically across all three image types (8xH200, B200, etc.)
5. First-boot reads config volume, configures ports via helm upgrade, and the VM is ready for gepetto-lite self-registration
6. The executor qcow2 can be deployed via `quick-launch.sh` (QEMU/KVM) by individual miners, same workflow as existing TEE VMs
7. A pre-built host OS image (with executor qcow2 baked in) is iPXE-bootable for data center provisioning
8. Data center iPXE path: cloud-init delivers miner credentials and Intel PCCS API key, first-boot service templates config.yaml and runs `quick-launch.sh` to auto-launch the VM
9. Renaming `site.yml` to `tee-gpu-vm.yml` produces byte-identical images (no behavioral change)
10. System manager accessible for monitoring on all variants
11. Host update service accepts validator-signed update commands, atomically downloads new VM images, and restarts the VM without manual data center intervention

---

## Constraints

- Zero behavioral changes to existing TEE GPU-worker image -- `site.yml` rename to `tee-gpu-vm.yml` is the only change
- Config volume schema changes are backward-compatible (new fields optional, existing VMs ignore them)
- No TEE/non-TEE conditionals inside Ansible roles -- separation is at the playbook level only
- Non-TEE executor image must not contain LUKS encryption or initramfs attestation hooks (but does include harden-access and prime-vm)
- GPU passthrough code in `host-tools/` is shared and unchanged across all variants
- No new Python dependencies
- Follow existing naming conventions (`ansible/guest/roles/`, `ansible/host/roles/`, `host-tools/scripts/`)
- Group rename (GID 1000) must preserve the numeric GID to avoid breaking existing cache volumes and pod permissions

---

## Output Format

1. **Directory rename**: `ansible/k3s/` -> `ansible/guest/` (pairs with new `ansible/host/`, accurately describes guest VM image build)
2. **Playbook rename**: `site.yml` -> `tee-gpu-vm.yml` (behavioral no-op)
3. **TEE executor playbook**: `ansible/guest/playbooks/tee-executor-vm.yml` -- shared roles + TEE roles + chutes-executor chart role
4. **Non-TEE executor playbook**: `ansible/guest/playbooks/executor-vm.yml` -- shared roles + harden-access + prime-vm + chutes-executor chart role (no luks, no attestation-service)
5. **Executor chart role**: `ansible/guest/roles/chutes-executor/` -- installs chutes-executor Helm chart with default values
6. **First-boot port config**: `ansible/guest/roles/k3s/files/k3s-init-scripts/04-k3s-executor-ports.sh` -- reads port config from `/var/config`, runs `helm upgrade`, verifies services are healthy before proceeding
7. **Config volume update**: extend `create-config.sh` + `config-schema.json` for optional `agent_port`, `attestation_port`, `node_port_range_start`, `node_port_range_end`, `tee_mode` fields
8. **process-config.py update**: handle new optional fields, write port config to a location readable by first-boot scripts
9. **Node label update**: `02-k3s-label.sh` conditionally sets `chutes/tee=true|false` based on `tee_mode` from config volume
10. **Host provisioning playbooks**: `ansible/host/playbooks/tee-host.yml` (TDX + PCCS packages + KVM + host-tools) and `ansible/host/playbooks/host.yml` (KVM + host-tools, no TDX). Shared host roles under `ansible/host/roles/`.
11. **PCCS auto-configuration role**: `ansible/host/roles/pccs/` -- installs PCCS packages at build time. At deploy time, first-boot service reads Intel API key from cloud-init user-data, runs non-interactive `pccs-configure`, and completes `PCKIDRetrievalTool` registration. TEE-host-only.
12. **iPXE host image builder**: `host-tools/scripts/build-host-image.sh` -- runs host playbook against a vanilla Ubuntu 25.04 image, produces an iPXE-bootable host image with everything pre-installed.
13. **iPXE boot script**: `host-tools/ipxe/boot.ipxe` -- chain-loads host image for data center provisioning.
14. **First-boot host service**: systemd service that (a) writes the complete `config.yaml` from cloud-init user-data to disk (same format as `config.tmpl.yaml` -- miner creds, network, volumes, ports, all provided by the DC provisioning server), (b) on TEE hosts, reads Intel PCCS API key from cloud-init and runs non-interactive PCCS configuration + `PCKIDRetrievalTool`, (c) runs `quick-launch.sh` with the config. The DC provisioning server is the source of truth for all per-machine configuration -- no auto-detection.
15. **Executor inventory template**: `ansible/guest/playbooks/inventory/executor.yml` (no TDX base image references)
16. **Host update service**: `host-tools/scripts/chutes_host/update_service.py` -- FastAPI service running on the host. Exposes `/update` endpoint authenticated via validator SR25519 signatures (reuses `sek8s/services/util.py` auth pattern with `ALLOWED_VALIDATORS`). Handles: repo pull, image download + SHA256 verification, atomic VM restart via `quick-launch.sh`. Reports status via `/status` endpoint. Installed as a systemd service by the host provisioning playbook.
17. **Host update service Ansible role**: `ansible/host/roles/update-service/` -- installs the update service as a systemd unit on the host (not in k3s), configures `ALLOWED_VALIDATORS` from cloud-init or Ansible vars, opens a dedicated port on the host's public interface via iptables.
18. **Group name cleanup**: Rename `tdx` group (GID 1000) to a purpose-descriptive name (e.g., `chutes-data`) across `ansible/guest/roles/system-manager/`, `ansible/guest/roles/cache-volume/`, `sek8s/system_manager/cache/manager.py`, and any chart values. `tdx-attest` (GID 987) stays as-is since it is legitimately TEE-specific, and is only created in TEE playbook variants.

---

## Failure Conditions

- Renamed `tee-gpu-vm.yml` produces different images than the original `site.yml`
- Non-TEE executor image contains LUKS or requires TDX hardware to boot
- TEE executor image missing attestation-service or LUKS encryption
- Executor images missing chutes-executor chart or Redis
- Config volume changes break existing TEE VM first-boot flow
- GPU passthrough behavior differs between any image variants
- Non-TEE executor qcow2 fails to launch via `quick-launch.sh` on a standard (non-TDX) miner host
- iPXE host image fails to boot or auto-provision the executor VM
- iPXE first-boot service fails to template config.yaml from cloud-init or pass it to quick-launch.sh
- Cloud-init user-data missing required config.yaml fields (network, volumes, miner creds), causing quick-launch.sh to fail or use wrong configuration
- PCCS not auto-configured on TEE host, causing QGSD to fail and TDX attestation to break at VM boot
- Host provisioning playbook fails to install TDX or PCCS non-interactively
- Host image missing required dependencies (aria2c, Python, PyYAML) causing quick-launch.sh or update service to fail
- First-boot helm upgrade fails silently, leaving services on wrong ports
- Gepetto-lite self-registration races ahead of port configuration, advertising default ports instead of configured ones to the validator
- Helm upgrade changes NodePort values but does not restart pods, leaving services bound to old ports
- System manager fails on non-TEE because group rename was incomplete (references to old `tdx` group name remain in some roles or Python code)
- Cache volume setup fails because GID 1000 group name changed but pods still reference the old name
- Host update service accepts unsigned or incorrectly signed requests, allowing unauthorized VM restarts
- Host update service stops the old VM before the new image is fully downloaded/verified, causing downtime
- Host update service fails to restart the VM after a successful image update, leaving the host with no running VM
- Host update service uses `quick-launch.sh --clean` (QEMU kill) instead of graceful system-manager shutdown, causing workload disruption
- Graceful shutdown hangs indefinitely because timeout/fallback to QEMU kill is missing
- Host update service not reachable from the validator due to missing host firewall rules (this is a host-level port, not a k3s NodePort)

---

## Rollout Notes

- **Phase 1**: Rename `ansible/k3s/` -> `ansible/guest/`. Rename `site.yml` -> `tee-gpu-vm.yml`. Create `executor-vm.yml` + `tee-executor-vm.yml` + chutes-executor role. Group name cleanup (`tdx` -> `chutes-data`). Update AGENT.md references. Build and test all three image types.
- **Phase 2**: Config volume port extensions + first-boot helm upgrade + node label update. Test end-to-end with chutes-miner gepetto-lite self-registration.
- **Phase 3**: Host provisioning playbooks (`ansible/host/`). `tee-host.yml` wraps TDX setup + PCCS package installation. `host.yml` for non-TEE. Both install KVM, host-tools, and bake in the guest qcow2. Build iPXE-bootable host images from playbook output. Cloud-init templates `config.yaml` with miner creds and Intel PCCS API key (TEE). First-boot service runs `quick-launch.sh`. Test with target data center hardware (DHCP + iPXE chain boot).
- **Phase 4**: Host update service. Validator-authenticated remote update endpoint on the host. Atomic image download + VM restart. Test update cycle end-to-end: validator triggers update, service pulls new image, stops old VM, relaunches.
- **Future**: Native bare metal image type (no VM layer, native GPU drivers). Separate spec when executor VM is proven.
- **Backward compatibility**: Renaming `site.yml` to `tee-gpu-vm.yml` is the only change to existing flow. All config volume extensions are optional fields with sensible defaults. Existing TEE deployments are unaffected.
- **No feature flags**: Image type determined by which playbook runs. No runtime detection.
- **Coordinated with chutes-miner**: chutes-executor chart will exist before this work begins (confirmed). The chutes-miner spec covers gepetto-lite, registration API, and cluster-scoped reconciliation.
