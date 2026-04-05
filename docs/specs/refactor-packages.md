# Refactor Spec: Monorepo layout, package split, and version surfaces

**Date**: 2026-04-02  
**Status**: draft  
**Delivery:** **Phased** — Phase 1 = `src/` + **`sek8s-common`** + version/CI hygiene; Phase 2 = **attestation-proxy** only. **No** per-VM-service package split in this spec.

---

## Motivation

- **Deployment reality:** **All guest VM services** (admission-controller, system-manager, system-status, attestation *service*, etc.) share **one install** on the VM with **multiple Poetry entrypoints**—by design. **Only attestation-proxy** runs **in k3s** as a **separate container image**. This refactor keeps **one VM package (`sek8s`)** and splits out **only** the cluster workload.
- **Unclear release surfaces:** The same tree today mixes **VM install** and **cluster-only proxy** code, so CI/CD cannot reliably separate **VM image version** bumps from **proxy package / image** bumps.
- **Minimal split:** Extract **attestation-proxy** (+ **`sek8s-common`**) so **proxy-only** changes can bump **`src/attestation-proxy`** and its image **without** implying a **guest VM image** bump—**the main attribution goal**—without splitting every VM daemon into its own package.
- **Version files conflate concerns:** **Root `VERSION`**, **`sek8s/VERSION`**, and **`pyproject.toml` `version`** drift across VM vs Poetry; **VM id** moves under **Ansible**; **per-package `VERSION` + pyproject sync** for **`sek8s`**, **`sek8s-common`**, **`attestation-proxy`** (after Phase 2).
- **Scaling VM configs:** **Ansible-local VM `VERSION`** supports **multiple guest profiles** without a single repo-root file.
- **Defer churn:** Per-VM-service packages (e.g. standalone admission-controller) are **out of scope**; revisit **only if** a later need warrants it.

**Non-goal:** Repository layout parity with [chutes-miner](https://github.com/chutesai/chutes-miner). That repo is a **mechanical reference** only (e.g. `src/`, `docker/<pkg>/`, Make patterns, `VERSION` → `pyproject` sync workflow).

---

## Phased delivery

Work is **intentionally sequenced** across PRs (or clearly scoped commits) with **integration checkpoints**:

| Phase | Goal | What ships |
|--------|------|------------|
| **Phase 1 — Structure** | Move to **`src/`** layout, introduce **`src/sek8s-common`**, align **VM `VERSION` → Ansible**, **Python 3.12**, meta **`pyproject.toml`**, Docker/Make/CI/Ansible for **today’s** VM + dev story. | **`src/sek8s`** + **`src/sek8s-common`** only. **All VM services** (including admission-controller, system-manager, attestation *service*, etc.) and **attestation-proxy implementation** remain **inside `src/sek8s`** until Phase 2—**same entrypoints**, new paths. **Checkpoint:** lint, test, guest install, smoke—**no** separate proxy package yet. |
| **Phase 2 — Attestation-proxy** | Extract **only** what runs **in k3s**: **`src/attestation-proxy`** + **`docker/attestation-proxy/`**; **sek8s → sek8s-common**, **attestation-proxy → sek8s-common**; **no** **sek8s ↔ attestation-proxy** package deps. | **Three** packages under `src/`. CI distinguishes **`src/attestation-proxy/**`** (cluster image) from **`src/sek8s/**`** + **`ansible/**`** (VM). **Checkpoint:** proxy workload vs VM services validated **independently**. |

**Rationale:** Phase 1 validates **tree + common + VM versioning** without rewiring the proxy. Phase 2 is the **smallest** split that matches **ops** (single VM install vs one cluster image). **Admission-controller** and other VM daemons **stay in `sek8s`** unless a **later** spec says otherwise.

---

## Scope

**In scope (across phases; see table above for timing)**

- **Python packages (end state of this spec):** **`src/sek8s`** (all **VM** services + entrypoints), **`src/attestation-proxy`** (k3s only), **`src/sek8s-common`**. **Not** separate packages per VM daemon.
- **Root meta–`pyproject.toml`:** Path dependencies with `develop = true` on each **existing** package in that phase (Phase 1: **sek8s** + **sek8s-common**; Phase 2 adds **attestation-proxy**).
- **Per-package `VERSION` as source of truth** for Poetry: every package under `src/<pkg>/` has **`VERSION`** + synced **`[tool.poetry] version`** (script + CI; see [chutes-miner `pyproject-version-check.yml`](https://github.com/chutesai/chutes-miner/blob/main/.github/workflows/pyproject-version-check.yml) and [`scripts/sync_pyproject_versions.py`](https://github.com/chutesai/chutes-miner/blob/main/scripts/sync_pyproject_versions.py)). **Phase 1:** **sek8s** + **sek8s-common**. **Phase 2:** add **attestation-proxy**.
- **VM / guest image version:** **Move off repo root** into **`ansible/`** (exact path TBD per playbook/role/image pipeline); support **multiple VM profiles** over time, each with its own version identifier.
- **Docker:** **Phase 1:** **`docker/sek8s/`** (or VM dev image) including **sek8s-common**. **Phase 2:** **`docker/attestation-proxy/`** for the **cluster** workload only.
- **Makefiles:** Package-scoped targets where useful (optional second goal = `src/` package name)—pragmatic, not ceremony for its own sake.
- **Ansible:** Guest **`pip install`** story for **`sek8s` + `sek8s-common`** only; **attestation-proxy** remains **container image** (not VM editable install) unless explicitly changed.
- **CI workflows:** **Phase 1:** **`src/sek8s/**`**, **`src/sek8s-common/**`**, **`ansible/**`**. **Phase 2:** add **`src/attestation-proxy/**`** so **proxy-only** changes require **proxy `VERSION`** / image bump **without** forcing **VM `VERSION`** when rules say so. Version-tag and security gates; optional split of “bump check” vs “pyproject sync” into separate jobs (same idea as distinct package-version vs pyproject-version checks).
- **Tests / coverage:** `pytest`, `--cov`, `docker/scripts/test.sh`, and lint file globs updated for new paths and multiple packages.
- **Python 3.12 alignment:** **3.12 is the canonical runtime** (Docker images and operational reality). As part of this refactor, align **[AGENT.md](../../AGENT.md)**, **root and per-package `pyproject.toml`** `python =` constraints, **[.github/workflows/ci.yml](../../.github/workflows/ci.yml)** (and any other workflows using `setup-python`), **[system-manager-openapi.yml](../../.github/workflows/system-manager-openapi.yml)** if needed, and **documented** expectations so **local dev, CI, and containers** all target **3.12** (consistent with [docker/Dockerfile](../../docker/Dockerfile) `FROM python:3.12`). Optionally add repo **`.python-version`** for local tooling.
- **[AGENT.md](../../AGENT.md):** Update hard rules that still say Python only under top-level `sek8s/` and **root `VERSION`** for releases once the new layout is decided; set stack line to **Python 3.12** (or **3.12+** if a future upper bound is kept in `pyproject.toml`).

**Out of scope (later phase / optional follow-on)**

- **Separate Python packages** for individual **VM-only** services (e.g. admission-controller, system-manager as standalone packages)—**not** part of this spec; split **later only if** warranted.
- GitHub Releases page process, CHANGELOG maintenance, and **broad** semver/release documentation redesign.
- Folding **`nvevidence/`** into `src/` and the root Poetry meta-project (optional follow-on PR).

**Not required in Phase 1**

- **`src/attestation-proxy`** as its own tree (**Phase 2**). **`src/sek8s-common`** is required starting **Phase 1**.

**Key files / areas today (implementation will relocate or split)**

- [pyproject.toml](../../pyproject.toml), [poetry.lock](../../poetry.lock), [Makefile](../../Makefile), [makefiles/](../../makefiles/)
- [sek8s/](../../sek8s/) (services include [sek8s/services/attestation_proxy.py](../../sek8s/services/attestation_proxy.py))
- [docker/Dockerfile](../../docker/Dockerfile), [docker/docker-compose.yaml](../../docker/docker-compose.yaml)
- [ansible/k3s/roles/sek8s/tasks/install-sek8s.yml](../../ansible/k3s/roles/sek8s/tasks/install-sek8s.yml)
- [.github/workflows/](../../.github/workflows/) (notably [ci.yml](../../.github/workflows/ci.yml), [version-tag.yml](../../.github/workflows/version-tag.yml), [system-manager-openapi.yml](../../.github/workflows/system-manager-openapi.yml), [security-verified-path-gate.yml](../../.github/workflows/security-verified-path-gate.yml))
- Root [VERSION](../../VERSION), [sek8s/VERSION](../../sek8s/VERSION)

---

## Before / After

| Before | After (by phase) |
|--------|------------------|
| Single package tree `sek8s/` at repo root; one Docker build `docker/Dockerfile` with `PROJECT_DIR=sek8s` | **Phase 1:** `src/sek8s` + `src/sek8s-common`; **`docker/sek8s/`** (or equivalent). **Phase 2:** add **`src/attestation-proxy`** + **`docker/attestation-proxy/`** (k3s image only). |
| VM services + proxy code in one tree | **Phase 1:** all still in **`src/sek8s`** (multiple **entrypoints** for VM). **Phase 2:** **only** **attestation-proxy** moves out. **Admission** and other VM daemons **stay in `sek8s`**. Shared code in **`sek8s-common`**; **sek8s** and **attestation-proxy** depend on **common** only—not on each other. |
| Root `VERSION` + `sek8s/VERSION` + `pyproject.toml` version conflate VM vs packages | **VM image version** under **`ansible/`**; **each** `src/<pkg>/` gets **`VERSION`** (SoT) + **synced** `[tool.poetry] version` when that package exists (Phase 1: **sek8s** + **sek8s-common**; Phase 2: + **attestation-proxy**). |
| CI gates on coarse `sek8s/**` / root `VERSION` | **Phase 1:** `src/sek8s/**`, `src/sek8s-common/**`, `ansible/**`. **Phase 2:** add **`src/attestation-proxy/**`**. |
| `make` / `images.mk` oriented around single `docker/` layout | **`docker/sek8s/`** (VM/dev bundle) and **`docker/attestation-proxy/`** (cluster); optional **`make <target> <pkg>`** |
| Ansible copies `sek8s/` + root `pyproject.toml` into guest | **`sek8s` + `sek8s-common`** on VM. **Attestation-proxy** as **container**, not VM **pip** install (unless explicitly changed). |
| **Python:** AGENT / `pyproject` / CI say **3.10+** or **3.10** in places; Docker uses **3.12** | **Single story: 3.12** in AGENT, all `pyproject.toml` files, GitHub Actions, and docs; guest venv matches policy |

---

## Goal

**Phase 1 complete** when:

1. **`src/sek8s`** and **`src/sek8s-common`** exist with **separate `pyproject.toml`**; root **path-depends** both with `develop = true`; **sek8s** path-depends **sek8s-common**.
2. **Version policy** applies to **every package that exists:** **`VERSION` SoT** + **synced** `[tool.poetry] version` for **sek8s** and **sek8s-common** (script + CI).
3. **VM version** under **`ansible/`**; **Makefile**, **version-tag**, **AGENT.md** updated; no long-term reliance on repo-root **`VERSION`** for VM id.
4. **CI** uses **`src/sek8s/**`**, **`src/sek8s-common/**`**, **`ansible/**`** (and related) for path → bump rules.
5. **Builds/tests** green; **guest install** works; **Python 3.12** aligned per spec.
6. **Behavior:** No intentional external behavior change (structural / hygiene only).

**Phase 2 complete** when, in addition: **`src/attestation-proxy`** exists; root meta + **sek8s** + **attestation-proxy** + **sek8s-common** wired; **`docker/attestation-proxy/`**; proxy image/workload validated; CI includes **`src/attestation-proxy/**`**; **no** **sek8s ↔ attestation-proxy** direct dependency; **path → bump** rules allow **proxy-only** changes to target **attestation-proxy `VERSION`** / image **separately** from **Ansible VM `VERSION`** where designed.

**Cross-cutting:** **Undocumented** policy for **sek8s-common API** changes vs **consumer bumps** (**sek8s** + **attestation-proxy**) must be explicit **by end of Phase 2**.

---

## Constraints

- Obey [AGENT.md](../../AGENT.md): Poetry 2.x, FastAPI/Uvicorn, pytest, existing lint stack; **no new dependencies** without discussion.
- **Python 3.12:** This refactor **includes** aligning the repo on **3.12** everywhere it matters: **AGENT.md**, **Poetry `python` constraints** (root + packages), **GitHub Actions**, and consistency with [docker/Dockerfile](../../docker/Dockerfile). **3.10** must not remain the **documented or CI default** for sek8s after the work is complete.
- Preserve **`sek8s` as the import name** for the main guest package unless the spec implementation explicitly documents a breaking rename for operators.
- **Shared library name:** **`sek8s-common`** — directory **`src/sek8s-common/`**, Poetry package name **`sek8s-common`**, Python import package **`sek8s_common`** (underscore in code per PEP 8; hyphen in repo/poetry for continuity with **sek8s** branding).
- **Attestation-proxy** extraction is **Phase 2** only—do not block Phase 1 on it. **VM daemons** (including **admission-controller**) **remain in `sek8s`** per this spec.
- Do not expand into **`nvevidence/`** merge or release-note processes beyond what this spec already lists as out of scope.

---

## Output Format

Implementation work should touch at least the following (exact diffs belong in implementation PRs, not this spec):

**Phase 1**

1. **`src/sek8s/`**, **`src/sek8s-common/`** with **`VERSION`**, **`pyproject.toml`** (**3.12**), package source; migrate from top-level **`sek8s/`**; **leave** attestation-proxy code **inside `src/sek8s`** until Phase 2 (along with **all other VM** services).
2. **Root** `pyproject.toml` (meta + path deps to **sek8s** + **sek8s-common**), **`poetry.lock`**; remove obsolete top-level **`sek8s/`** when done.
3. **`scripts/sync_pyproject_versions.py`** (or equivalent) for **`src/*`** modules present in Phase 1; extend in later phases.
4. **`docker/sek8s/`** (or VM bundle) including **sek8s-common**; Makefiles as needed.
5. **Ansible:** VM **`VERSION`** location(s); [install-sek8s.yml](../../ansible/k3s/roles/sek8s/tasks/install-sek8s.yml) for **sek8s** + **sek8s-common**.
6. **CI / AGENT / 3.12** per spec; path filters for **`src/sek8s/**`**, **`src/sek8s-common/**`**, **`ansible/**`**.

**Phase 2**

7. **`src/attestation-proxy/`** + **`docker/attestation-proxy/`**; move **only** proxy implementation + **`[tool.poetry.scripts]`**; root meta + **path deps**; extend sync script and CI for **`src/attestation-proxy/**`**.

**All phases:** [makefiles/development.mk](../../makefiles/development.mk), [makefiles/images.mk](../../makefiles/images.mk), [Makefile](../../Makefile), [ci.yml](../../.github/workflows/ci.yml), [version-tag.yml](../../.github/workflows/version-tag.yml), [security-verified-path-gate.yml](../../.github/workflows/security-verified-path-gate.yml), [system-manager-openapi.yml](../../.github/workflows/system-manager-openapi.yml) as paths change; **tests** / coverage globs updated per package; [README.md](../../README.md) minimal if needed.

---

## Failure Conditions

**Phase 1 must not merge** if:

- **Guest VM** cannot install or run services (paths, venv, units broken without documented operator migration).
- **`VERSION` / pyproject** diverge for **`sek8s`** or **`sek8s-common`** where those packages exist.
- **VM-scoped changes** merge without updating **Ansible VM `VERSION`** when required.
- **CI** still depends only on repo-root **`VERSION`** or top-level **`sek8s/**`** after the tree lives under **`src/`**.
- **Python** story still centered on **3.10** for dev/CI.

**Phase 2 must not merge** if:

- **Attestation-proxy** image or workload **fails** (wrong context, missing **sek8s-common**, broken entrypoints).
- **Direct** **`sek8s` ↔ `attestation-proxy`** package dependency (forbidden; use **sek8s-common**).

**Any phase:** **Undocumented** **sek8s-common API → consumer bump** policy once **sek8s** and **attestation-proxy** both consume **common**.

---

## Risks

- **Phase boundary confusion:** Merging **Phase 2** before **Phase 1** is validated in production-like checks; **mitigation:** explicit **checkpoint** and PR title / description labeling phase.
- **Circular or sideways imports** when proxy splits; enforce **DAG: sek8s-common ← sek8s** and **sek8s-common ← attestation-proxy** (**no** **sek8s ↔ attestation-proxy** edges).
- **sek8s-common becomes a junk drawer**; keep extraction **minimal** and justified by **dual use**.
- **Miner sync script assumptions** (`src/<module>/<package>/` path patterns with `-` → `_`) may not match sek8s; **adapt script or directory layout** explicitly.
- **Docker build context** too large or missing **sek8s-common**; **duplicate** dependency resolution between images.
- **Ansible** must install **two** (or more) local packages; **lockfile** and **editable install** story must stay coherent.
- **Operator-facing** systemd units, container images, and miner automation **expect old paths or image names**—require **compat notes** or **versioned migration** in implementation PR description.
- **Multiple Ansible VM `VERSION` files** later: CI must remain **understandable** (document matrix: path prefix → which file to bump).

---

## Migration Strategy

**Phase 1 (structure — merge and validate before Phase 2)**

1. **Decide** Ansible VM `VERSION` path(s) and **root `VERSION` deprecation** (optional short-term symlink/duplicate if needed).
2. **Align Python to 3.12** in AGENT, `pyproject.toml`, CI.
3. **Introduce `src/sek8s-common`**; extract **only** modules that are already shared or will be shared by upcoming splits (keep minimal).
4. **Move** package to **`src/sek8s`**; **all VM services** and **proxy code** **stay** under **`src/sek8s`** until Phase 2.
5. **Docker** (`docker/sek8s/` or equivalent), **Make**, **Ansible** install (**sek8s** + **sek8s-common**), **CI** path filters, **VERSION sync** for packages that exist.
6. **Remove** top-level **`sek8s/`** when readers are updated. **Stop and test** (VM + cluster smoke as appropriate).

**Phase 2 (attestation-proxy)**

7. **Create `src/attestation-proxy`**; move proxy implementation + **`[tool.poetry.scripts]`**; add **`docker/attestation-proxy/`**; wire **path deps**; extend CI/sync. **Stop and test** proxy workload independently.

**Rollback:** Revert the merge branch; keep changes **shippable in chunks** where possible so CI stays green between steps.

**Keeping CI/Ansible green:** After each logical step, run **lint, unit tests, and** (where feasible) **guest-relevant** playbook dry-runs or image builds; update **workflow path filters** in the same PR that moves files so `main` never has **dead** glob patterns.

**Backward compatibility for miners/operators:** Document **image names**, **version semantics**, and **any** required **inventory/playbook** changes in the **implementation PR** body; avoid silent breakage of **attestation** or **admission** paths.

---

## Open decisions (resolve during implementation)

1. **Exact path** for VM image `VERSION` under `ansible/k3s/` (and naming when **N > 1** guest profiles exist).
2. **CI strictness** when only `src/sek8s-common/**` changes: bump **sek8s-common only**, or **require** **sek8s** and/or **attestation-proxy** version bumps (policy explicit **before or when Phase 2 completes**).
3. Default assumption: **attestation-proxy** is **container-only** on the guest; document if **pip install** on VM is ever required.
4. **Single** combined workflow vs **split** jobs for **version bump check** vs **pyproject sync commit** (e.g. separate `package-version-check` vs `pyproject-version-check` jobs).

**Resolved in this spec:** **Python 3.12** is the target for AGENT, Poetry, CI, and containers; no separate “pick a version” decision required unless the team later changes the upper bound in `pyproject.toml`. **Shared library:** **`sek8s-common`** (`src/sek8s-common/`, Poetry **`sek8s-common`**, import **`sek8s_common`**). **VM services** stay in **one `sek8s` package**; **only attestation-proxy** splits out for **k3s**; **no** admission-controller (or other VM daemon) package split in this work.
