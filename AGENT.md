# Agent Constraints

**Read this file before making any changes.** These constraints apply to all AI-assisted work in this repository.

## Project Identity

sek8s is confidential GPU infrastructure for Chutes miners and zero-trust workloads. This repo bundles everything needed to build, attest, launch, and operate Intel TDX VMs with NVIDIA GPUs — including host orchestration scripts, guest image builder, attestation services, admission control, and integration with the chutes-miner control plane.

## Stack (Non-Negotiable)

- **Language**: Python 3.12+ (sek8s packages under `src/`), Bash (host-tools, guest-tools scripts)
- **Package manager**: Poetry 2.x
- **HTTP services**: FastAPI + Uvicorn (admission controller, attestation, system manager/status)
- **Policy engine**: OPA (admission controller policies in `ansible/guest/roles/admission-controller/files/policies/`)
- **Provisioning**: Ansible (guest image build in `ansible/guest/`)
- **Orchestration**: k3s (inside guest VM)
- **Container signing**: cosign
- **Linting**: bandit, black, flake8, isort, mypy
- **Testing**: pytest, pytest-asyncio

Do not introduce alternate frameworks (e.g., Prisma, NextAuth, Firebase). Stay within this stack.

## Hard Rules

- **Never install a new dependency** without discussion first
- **Never modify database schemas** without showing the migration plan (sek8s has no DB; this applies if one is added)
- **Python services**: Poetry packages under `src/sek8s/` (import name `sek8s`), `src/sek8s-common/` (`sek8s_common`), and `src/attestation-proxy/` (`attestation_proxy`); tests under `tests/`
- **Shell scripts** in `host-tools/scripts/` and `guest-tools/`
- **Ansible roles** in `ansible/guest/roles/`
- **OPA policies** in `ansible/guest/roles/admission-controller/files/policies/`
- **Environment variables** go in config files (pydantic-settings, Ansible vars) — never hardcoded
- **90% test coverage target** — if you change code, add tests for it
- **No class-based tests** — use plain functions (`def test_*`) with fixtures, not `class Test*` groupings
- **Run `make lint-local` and `make reformat`** before committing
- **Never commit or alter git history** without explicit human approval for that specific action — including `git commit`, `git commit --amend`, rebase, history-changing `reset`, `cherry-pick`, branch delete, or force-push. Leave changes for the author to review and commit unless they clearly asked you to perform a named git operation.
- **Never modify Ansible roles** without understanding the guest image build pipeline
- **Never hardcode attestation keys or measurements**
- **Version bumps** — Three domains; see [docs/versioning.md](docs/versioning.md) for the full policy. **VM domain** (`ansible/guest/*`, `src/sek8s/*`, `src/sek8s-common/*`, `nvevidence/*`, root `pyproject.toml`/`poetry.lock`): bump `ansible/guest/VERSION`. Changes under **`ansible/host/`** do not bump the guest image version. **Proxy domain** (`src/attestation-proxy/*`): bump `src/attestation-proxy/VERSION`. **Ops domain** (`ansible/host/*`, `host-tools/*`, `.github/workflows/*`): bump `changelogs/ops/VERSION` using CalVer `YYYY.MM.PATCH` (e.g. `2026.05.0`; increment PATCH for a second release in the same month). Per-package `VERSION` files are the source of truth for `[tool.poetry] version` — keep them in sync via `scripts/sync_pyproject_versions.py`. Version bumps happen at release time, not during feature development.
- **Changelog fragments** — As you make changes, keep `changelogs/<component>/unreleased/<branch-name>.md` up to date using [Keep a Changelog](https://keepachangelog.com/) category headers (`### Added`, `### Changed`, `### Fixed`, `### Removed`). This is the only changelog file you should ever touch during development. **Never write `## [x.y.z]` version headings or edit `CHANGELOG.md` directly** — that is done by `make promote-changelogs` (or CI) at release time. PRs to `main` must have no unreleased fragments remaining.

## Patterns

- **Async-first**: Use `async def`, `aiohttp`, etc. for FastAPI services. Avoid blocking calls in request handlers
- **Pydantic models** for request/response schemas (in `src/sek8s/sek8s/models.py`, `src/sek8s/sek8s/responses.py`, per-module `models.py`)
- **Provider pattern** for hardware abstraction (`src/sek8s/sek8s/providers/tdx.py`, `gpu.py`, `nvtrust.py`)
- **Validator pattern** for admission control (`src/sek8s/sek8s/validators/base.py`, `opa.py`, `cosign.py`, `registry.py`)
- **Config via pydantic-settings** (`src/sek8s/sek8s/config.py`)
- **Custom exceptions** in `src/sek8s/sek8s/exceptions.py`
- **Shell scripts**: use `set -euo pipefail`, quote variables, use functions for reusable logic
- **One concern per module** — keep files focused; split when they grow large
- **Follow existing naming** — check neighboring files and packages for conventions

## Architecture Overview

| Component | Purpose |
|-----------|---------|
| **src/sek8s/sek8s/services/** | FastAPI services: admission-controller, attestation, system-manager |
| **src/sek8s/sek8s/providers/** | Hardware abstraction: TDX quotes, GPU info (nvidia-ml-py), NVIDIA trust |
| **src/sek8s/sek8s/validators/** | Admission validators: OPA, cosign, registry |
| **src/sek8s/sek8s/system_manager/** | System manager sub-routers: images, status, cache |
| **src/sek8s-common/sek8s_common/** | Shared config, server, auth, and constants for all sek8s packages |
| **src/attestation-proxy/attestation_proxy/** | Dual-port attestation proxy (separate lean Docker image) |
| **nvevidence/** | NVIDIA attestation SDK wrapper (separate Poetry package) |
| **host-tools/** | Host setup (`chutes_cvm.host`), GPU binding/VM launch (`chutes_cvm.guest`), networking, orchestration (`quick-launch.sh`) |
| **guest-tools/** | TDX VM image builder, boot measurement extraction |
| **ansible/guest/** | Ansible roles for guest image build (k3s, GPU drivers, attestation services, LUKS) |
| **ansible/host/** | Operational Ansible (setup / launch / upgrade) for bare-metal TDX hosts over SSH |
| **opa/** | OPA policy files for admission controller |
| **guest-tools/** | Guest measurement & verification tooling (`measurement/`), image build output (`image/`), and R2 publish (`publish-image.sh`) |

## Environment Setup

Poetry is installed via `pipx` at `~/.local/bin/poetry` (not in the project virtualenv). Before running any shell commands, ensure `~/.local/bin` is on your `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

The project virtualenv lives at `.venv/` (created by `poetry install`). To activate it for ad-hoc commands:

```bash
source .venv/bin/activate
```

## Development Commands

Python tooling uses an optional **second make goal** = package directory under `src/` (`sek8s`, `sek8s-common`, `attestation-proxy`). Omit it to run **every** package.

```bash
make list-packages     # Show packages under src/
make lint-local                    # Lint all packages
make lint-local sek8s            # Lint only sek8s (black/flake8/isort include tests/ for sek8s)
make lint-local sek8s-common     # Lint only sek8s-common
make test-local                  # pytest with --cov for each package import
make test-local sek8s            # pytest with --cov=sek8s only
make reformat sek8s              # Format one package (+ tests when sek8s)
make generate-openapi            # Requires sek8s in selection (default "all" includes it)
```

Other targets:

```bash
make help              # List all targets
make venv              # Create virtual environment (poetry install)
make install           # Install dev dependencies (venv + OPA binary)
make lint              # Same as lint-local but inside Docker (per-package loop)
make test              # Same as test-local but inside Docker
make test-opa-policies # Run OPA policy tests
make build             # Build Docker images (PROJECT=sek8s by default)
make ci                # Full CI: clean, build, infrastructure, lint, test, clean
```

Package layout convention: `src/<name>/` with Python import path `src/<name>/<name_with_hyphens_as_underscores>/`.
