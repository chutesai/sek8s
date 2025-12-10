# sek8s

Confidential GPU infrastructure for Chutes miners and zero-trust workloads. This monorepo bundles everything you need to build, attest, launch, and operate Intel TDX VMs with NVIDIA GPUs—including the host orchestration scripts, the guest image builder, and ready-to-run documentation.

---

## What's in this repo?

| Area | Contents |
| --- | --- |
| `host-tools/` | Bare-metal host preparation, GPU/NVSwitch binding, bridge networking, cache/config volume creation, and VM launch scripts |
| `tdx/` | Submodule with Intel's upstream host enablement scripts |
| `guest-tools/`, `ansible/k3s/` | Tooling to build and validate the encrypted guest image (Ubuntu + k3s + attestation stack) |
| `docs/` | Operator-facing guides like the new end-to-end miner walkthrough |
| `sek8s/`, `nvevidence/`, `tests/` | Python services, attestation helpers, and validation suites used by Chutes infrastructure |

---

## Quick start roadmap

1. **Prepare the host** — Follow the [TDX VM Host Setup Guide](host-tools/README.md) to install the required kernel, PCCS, GPU bindings, and bridge networking.
2. **Understand the full workflow** — The [End-to-End Chutes Miner Setup](docs/end-to-end-miner.md) explains how host prep, VM launch, k3s, and the Helm-based miner deployment fit together.
3. **Customize or rebuild the guest image** — See [ansible/k3s/README.md](ansible/k3s/README.md) for details on producing the encrypted `tdx-guest.qcow2` image yourself.

After that, you can launch `host-tools/scripts/quick-launch.sh` to bind GPUs, create volumes, and boot the miner-ready VM in one shot.

> **Important:** The guest root disk is LUKS-encrypted. Only the Chutes attestation/key service (or your own compatible service) can decrypt it after verifying Intel TDX measurements, so simply possessing the qcow2 image is not enough to run the VM.

### How this repo pairs with `chutes-miner`

- The sek8s guest image already contains the Chutes stack; you do **not** run Helm/Ansible on the TEE VM itself.
- The [chutes-miner](https://github.com/chutesai/chutes-miner) repo is still required for your **control node** (inventory, scheduling, monitoring) and provides the `chutes-miner-cli` used to enroll both TEE and non-TEE nodes.
- Use the same control node for every worker, but **never** add a sek8s TEE VM to the chutes-miner Ansible inventory—there is no SSH access, so management happens via the CLI/API only.

---

## Repository layout (abridged)

```
sek8s/
├── host-tools/        # Host automation, docs, scripts
├── guest-tools/       # Guest image + measurement utilities
├── ansible/k3s/       # Image build playbooks
├── docs/              # Operator guides
├── nvevidence/        # Evidence verification service
├── sek8s/             # Python services + APIs
└── tests/             # Integration/unit suites
```

---

## Questions / contributions

- File an issue or PR in this repo for host tooling, image builds, or docs
- Use the [chutes-miner](https://github.com/chutesai/chutes-miner) repo for chart-specific issues
