# Feature Spec: Changelog Support

**Date**: 2026-04-04  
**Status**: done

---

## Context

- **Packages affected**: `sek8s`, `attestation-proxy`, VM image line (`ansible/k3s/`)
- **Key files**: `docs/versioning.md`, `.github/workflows/version-tag.yml`, `scripts/promote_changelogs.py`
- **Dependencies**: None new (zero-dependency implementation)

The monorepo has clear version surfaces (see `docs/versioning.md`) with per-package
VERSION files, domain-based bump enforcement, and per-package git tags. This spec adds
a structured record of **what changed** in each version for internal operators deploying
VM images and anyone tracking the proxy container.

GitHub Releases are not needed: these packages are not published to PyPI or consumed
outside this repo. The audience is the team deploying and operating sek8s infrastructure.

---

## Design Decisions

### Tooling: fragment-based with idempotent automated promotion

Each feature branch drops a categorized `.md` fragment into
`changelogs/<component>/unreleased/`. Promotion aggregates fragments by category,
writes a versioned `## [x.y.z] - date` entry to `CHANGELOG.md`, and deletes the
fragments. Promotion is **idempotent**: if the version heading already exists, new
fragments are merged into the existing section. No external dependencies (towncrier
or similar). The fragment approach eliminates merge conflicts on `CHANGELOG.md`.

On `release/**` branches, the `changelog-auto-promote.yml` workflow auto-promotes
on each push. On PRs to `main`, a strict check enforces that no fragments remain.
Tags are created on merge to `main`.

### Scope: per-component changelogs in top-level directory

Changelogs live under `changelogs/`, one subdirectory per component, consistent with
the repo's top-level domain organization (`src/`, `ansible/`, `tests/`, `docs/`).

| Component | CHANGELOG | Fragments |
|-----------|-----------|-----------|
| VM image | `changelogs/vm/CHANGELOG.md` | `changelogs/vm/unreleased/` |
| sek8s | `changelogs/sek8s/CHANGELOG.md` | `changelogs/sek8s/unreleased/` |
| Proxy | `changelogs/attestation-proxy/CHANGELOG.md` | `changelogs/attestation-proxy/unreleased/` |

**No sek8s-common changelog.** Common is always consumed by sek8s or attestation-proxy;
changes are documented in the consumer's changelog.

### No skip mechanism

The domain version checks already ensure only functional code changes trigger VERSION
bumps. CI-only or docs-only changes don't touch version-checked paths, so changelog
enforcement never fires for them.

### Branching model support

Both trunk-based and release-branch workflows are supported. Feature branches add
fragments to `unreleased/`. On release branches, CI auto-promotes fragments on each
merge. For trunk-based, developers run `make promote-changelogs` before PR to main.
PRs to `main` enforce a strict "no fragments" policy.

### Fragment format

Files use [Keep a Changelog](https://keepachangelog.com/) category headers
(`### Added`, `### Changed`, `### Fixed`, `### Removed`) with bullet entries.
Filename derived from branch name (strip prefix): `feature/nvidia-590-drivers`
becomes `nvidia-590-drivers.md`.

### Versioned headings are automation-only

Developers never manually create `## [x.y.z]` headings in `CHANGELOG.md`. CI rejects
PRs where the versioned heading already exists. This ensures all changelog entries flow
through the fragment system and are aggregated consistently.

---

## API Changes

- **New endpoints**: None
- **Schema changes**: None
- **Migrations**: None

---

## Goal

Success =
1. Each version domain has a `CHANGELOG.md` and `unreleased/` directory under
   `changelogs/`.
2. CI auto-promotes fragments on `release/**` branches (idempotent).
3. CI enforces strict "no fragments" on PRs to `main`.
4. Tags are created on merge to `main` when changelogs are clean.
5. No new runtime or dev dependencies introduced.
6. `docs/versioning.md` and `AGENT.md` document the fragment workflow.

---

## Constraints

- No new dependencies without team discussion.
- Do not generate GitHub Releases.
- Strict "no fragments" enforcement on PRs to `main`; normal validation on PRs to `release/**`.
- Keep the format human-readable and diff-friendly.

---

## Output Format

1. `changelogs/<component>/CHANGELOG.md` — seeded with version history.
2. `changelogs/<component>/unreleased/` — fragment directories with `.gitkeep`.
3. `scripts/promote_changelogs.py` — `--check`, `--check --strict`, and `--promote` modes.
4. `.github/workflows/version-tag.yml` — strict check on PRs to main, tagging on push.
5. `.github/workflows/changelog-auto-promote.yml` — auto-promote on release branches.
6. `docs/versioning.md` — fragment workflow documentation.
7. `AGENT.md` — version bumps rule references fragment system.

---

## Failure Conditions

- Format drifts across fragments. Mitigation: start with human review; add format
  linting later if needed.
- Developer forgets to add a fragment. Mitigation: CI fails the PR when VERSION is
  bumped without fragments in `unreleased/`.

---

## Rollout Notes

- Each CHANGELOG.md seeded with existing version history from discord announcements.
- Existing `## [Unreleased]` content migrated to fragment files.
- No Ansible changes, no feature flags.
- Backward compatible: purely additive files + CI automation.
