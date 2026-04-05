# Feature Spec: Changelog Support

**Date**: 2026-04-04  
**Status**: done

---

## Context

- **Packages affected**: `sek8s`, `attestation-proxy`, VM image line (`ansible/k3s/`)
- **Key files**: `docs/versioning.md`, `.github/workflows/version-tag.yml`, per-package `VERSION` files
- **Dependencies**: None new (zero-dependency implementation)

The monorepo has clear version surfaces (see `docs/versioning.md`) with per-package
VERSION files, domain-based bump enforcement, and per-package git tags. This spec adds
a structured record of **what changed** in each version for internal operators deploying
VM images and anyone tracking the proxy container.

GitHub Releases are not needed: these packages are not published to PyPI or consumed
outside this repo. The audience is the team deploying and operating sek8s infrastructure.

---

## Design Decisions

### Tooling: manual CHANGELOG.md + CI gate (Option C)

Manual `CHANGELOG.md` files with a CI check that requires the changelog to contain a
`## [x.y.z]` heading matching the VERSION file when it's bumped. No new dependencies.
If fragment-based generation (e.g. towncrier) becomes valuable later, it layers on top
without rework.

Options A (towncrier) and B (manual with no enforcement) were considered and rejected.
Towncrier adds dev dependency friction; no enforcement leads to stale changelogs.

### Scope: per-component changelogs in top-level directory

Changelogs live under `changelogs/`, one subdirectory per component, consistent with
the repo's top-level domain organization (`src/`, `ansible/`, `tests/`, `docs/`).

| Changelog | Tracks |
|-----------|--------|
| `changelogs/vm/CHANGELOG.md` | VM image releases (operator-facing) |
| `changelogs/sek8s/CHANGELOG.md` | sek8s package changes |
| `changelogs/attestation-proxy/CHANGELOG.md` | Proxy-specific changes |

**No sek8s-common changelog.** Common is always consumed by sek8s or attestation-proxy;
changes are documented in the consumer's changelog.

### No skip mechanism

The domain version checks already ensure only functional code changes trigger VERSION
bumps. CI-only or docs-only changes don't touch version-checked paths, so changelog
enforcement never fires for them.

### Branching model support

The `## [Unreleased]` pattern from Keep a Changelog supports both workflows:

- **Trunk-based**: PR adds `## [x.y.z] - date` heading + entries directly.
- **Release branch**: features add entries under `## [Unreleased]`, release PR renames
  to `## [x.y.z] - date` and bumps VERSION.

CI checks for the **version heading in the file**, not just that the file was modified,
so both models satisfy the same invariant.

### Format

Standard [Keep a Changelog](https://keepachangelog.com/) format with categories:
Added, Changed, Fixed, Removed.

---

## API Changes

- **New endpoints**: None
- **Schema changes**: None
- **Migrations**: None

---

## Goal

Success =
1. Each version domain has a `CHANGELOG.md` under `changelogs/` following Keep a
   Changelog format.
2. CI requires the relevant `CHANGELOG.md` to contain a `## [x.y.z]` heading matching
   the VERSION file when it's bumped (same PR).
3. No new runtime or dev dependencies introduced.
4. `docs/versioning.md` and `AGENT.md` updated to reference the changelog policy.

---

## Constraints

- No new dependencies without team discussion.
- Do not generate GitHub Releases.
- CHANGELOG enforcement applies only on PRs to `main` (same as version-tag checks).
- Keep the format human-readable and diff-friendly.

---

## Output Format

1. `changelogs/vm/CHANGELOG.md` — seeded with `0.2.7` entry.
2. `changelogs/sek8s/CHANGELOG.md` — seeded with `0.2.5` entry.
3. `changelogs/attestation-proxy/CHANGELOG.md` — seeded with `0.1.0` entry.
4. `.github/workflows/version-tag.yml` — `check_changelog` function in version-check
   job verifies `## [x.y.z]` heading exists when VERSION is bumped.
5. `docs/versioning.md` — new "Changelogs" section with mapping table and workflow docs.
6. `AGENT.md` — version bumps rule updated to include changelog requirement.

---

## Failure Conditions

- Merge conflicts in CHANGELOG.md become chronic. Mitigation: unreleased section stays
  short; entries move to versioned sections on release.
- Format drifts across packages. Mitigation: start with human review; add format
  linting later if needed.

---

## Rollout Notes

- Each CHANGELOG.md seeded with existing version history (minimal — version numbers
  and dates with brief descriptions).
- No Ansible changes, no feature flags.
- Backward compatible: purely additive files + CI check.
