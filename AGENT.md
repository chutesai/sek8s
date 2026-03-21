# Agent Constraints

**Read this file before making any changes.** These constraints apply to all AI-assisted work in this repository.

## Project Identity

sek8s is confidential GPU infrastructure for Chutes miners and zero-trust workloads. This repo bundles everything needed to build, attest, launch, and operate Intel TDX VMs with NVIDIA GPUs — including host orchestration scripts, guest image builder, attestation services, admission control, and integration with the chutes-miner control plane.

## Stack (Non-Negotiable)

- **Language**: Python 3.10+ (sek8s services), Bash (host-tools, guest-tools scripts)
- **Package manager**: Poetry 2.x
- **HTTP services**: FastAPI + Uvicorn (admission controller, attestation, system manager/status)
- **Policy engine**: OPA (admission controller policies in `ansible/k3s/roles/admission-controller/files/policies/`)
- **Provisioning**: Ansible (guest image build in `ansible/k3s/`)
- **Orchestration**: k3s (inside guest VM)
- **Container signing**: cosign
- **Linting**: bandit, black, flake8, isort, mypy
- **Testing**: pytest, pytest-asyncio

Do not introduce alternate frameworks (e.g., Prisma, NextAuth, Firebase). Stay within this stack.

## Hard Rules

- **Never install a new dependency** without discussion first
- **Never modify database schemas** without showing the migration plan (sek8s has no DB; this applies if one is added)
- **Python services live under `sek8s/`**, tests under `tests/`
- **Shell scripts** in `host-tools/scripts/` and `guest-tools/`
- **Ansible roles** in `ansible/k3s/roles/`
- **OPA policies** in `ansible/k3s/roles/admission-controller/files/policies/`
- **Environment variables** go in config files (pydantic-settings, Ansible vars) — never hardcoded
- **90% test coverage target** — if you change code, add tests for it
- **Run `make lint-local` and `make reformat`** before committing
- **Never modify Ansible roles** without understanding the guest image build pipeline
- **Never hardcode attestation keys or measurements**
- **Version bumps** — `VERSION` file at root tracks the release version; update when releasing

## Patterns

- **Async-first**: Use `async def`, `aiohttp`, etc. for FastAPI services. Avoid blocking calls in request handlers
- **Pydantic models** for request/response schemas (in `sek8s/models.py`, `sek8s/responses.py`, per-module `models.py`)
- **Provider pattern** for hardware abstraction (`sek8s/providers/tdx.py`, `gpu.py`, `nvtrust.py`)
- **Validator pattern** for admission control (`sek8s/validators/base.py`, `opa.py`, `cosign.py`, `registry.py`)
- **Config via pydantic-settings** (`sek8s/config.py`)
- **Custom exceptions** in `sek8s/exceptions.py`
- **Shell scripts**: use `set -euo pipefail`, quote variables, use functions for reusable logic
- **One concern per module** — keep files focused; split when they grow large
- **Follow existing naming** — check neighboring files and packages for conventions

## Architecture Overview

| Component | Purpose |
|-----------|---------|
| **sek8s/services/** | FastAPI services: admission-controller, attestation, attestation-proxy, system-manager, system-status |
| **sek8s/providers/** | Hardware abstraction: TDX quotes, GPU info (nvidia-ml-py), NVIDIA trust |
| **sek8s/validators/** | Admission validators: OPA, cosign, registry |
| **sek8s/system_manager/** | System manager sub-routers: images, status, cache |
| **nvevidence/** | NVIDIA attestation SDK wrapper (separate Poetry package) |
| **host-tools/** | Host setup, GPU binding, networking, VM launch (`quick-launch.sh`) |
| **guest-tools/** | TDX VM image builder, boot measurement extraction |
| **ansible/k3s/** | Ansible roles for guest image build (k3s, GPU drivers, attestation services, LUKS) |
| **opa/** | OPA policy files for admission controller |
| **tdx/** | Git submodule: Intel TDX host enablement (Canonical) |

## Development Commands

```bash
make help              # List all targets
make venv              # Create virtual environment (poetry install)
make install           # Install dev dependencies (venv + OPA binary)
make test-local        # Run pytest with coverage
make lint-local        # Run bandit, black, flake8, isort, mypy
make reformat          # Format code (autoflake + isort + black)
make test-opa-policies # Run OPA policy tests
make build             # Build Docker images
make ci                # Full CI: clean, build, infrastructure, lint, test, clean
```
