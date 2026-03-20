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
- **Use Make commands for tooling** — never run `python`, `pytest`, or lint tools directly. The global Python interpreter does not have project dependencies installed. Use `make test-local`, `make lint-local`, `make reformat` instead.
- **Never modify Ansible roles** without understanding the guest image build pipeline
- **Never hardcode attestation keys or measurements**
- **Version bumps** — `VERSION` file at root tracks the release version; update when releasing

## Patterns

- **Single return per method** — Use one return at the end of each method with a clear path. Compute values first, then build the return. Avoid multiple early returns that scatter logic and make debugging harder.
- **Typed models over dicts** — Do not use arbitrary dictionaries to represent data. Use classes (dataclasses, Pydantic models) that define the structure. Dicts make changes hard to track and hide data contracts.
- **Classmethods for construction from other types** — Define conversion from one data type to another as classmethods on the target type. Keeps conversion logic in one place, clarifies input/output contracts, and documents the expected input format.
- **Async-first**: Use `async def`, `aiohttp`, etc. for FastAPI services. Avoid blocking calls in request handlers
- **Pydantic models** for request/response schemas (in `sek8s/models.py`, `sek8s/responses.py`, per-module `models.py`)
- **Provider pattern** for hardware abstraction (`sek8s/providers/tdx.py`, `gpu.py`, `nvtrust.py`)
- **Validator pattern** for admission control (`sek8s/validators/base.py`, `opa.py`, `cosign.py`, `registry.py`)
- **Config via pydantic-settings** (`sek8s/config.py`)
- **Custom exceptions** in `sek8s/exceptions.py`
- **Shell scripts**: use `set -euo pipefail`, quote variables, use functions for reusable logic
- **One concern per module** — keep files focused; split when they grow large
- **Follow existing naming** — check neighboring files and packages for conventions

## Unit Testing

- **Never mock the module under test** — Do not patch functions, classes, or methods inside the module you are testing without explicit approval. Mock external dependencies (subprocess, HTTP, filesystem) at the boundary where they are used.
- **Reusable fixtures with valid defaults** — Use fixtures that provide realistic, valid default behavior for external dependencies. Fixtures should be reusable across tests in the same domain.
- **Fixtures live in `tests/fixtures/`** — Split fixtures from test modules. Add domain-specific modules (e.g. `tests/fixtures/helm.py`, `tests/fixtures/process.py`) and import from `conftest.py` or test modules as needed.
- **`autouse=True` for process/host-affecting mocks** — Mock subprocess execution, network calls, and anything that could alter the host or have side effects. Use `autouse=True` on these fixtures so tests never accidentally hit real system calls.
- **No real sleeps or timeouts in unit tests** — Use `await asyncio.sleep(0)` to yield to the event loop when needed; never `sleep(0.1)` or similar. Mock timeouts, or use events/futures for synchronization.
- **Patch shared system deps at the source** — For dependencies like `asyncio.create_subprocess_exec`, patch at the module level (`asyncio.create_subprocess_exec`) so one fixture covers all consumers. Avoid per-module use-site patches that must be updated whenever new code uses the same dependency.
- **Test isolation** — Each test must be independent; avoid shared mutable state between tests.
- **One behavior per test** — Each test should verify a single behavior or outcome; split complex scenarios into multiple tests.

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

**Always use Make commands** — they run tools via the project's Poetry venv. Do not invoke `python`, `pytest`, or lint tools directly; the global interpreter lacks project dependencies.

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

For manual debugging, activate the venv first (e.g. `poetry run pytest ...` or `poetry shell`).
