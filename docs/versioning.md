# Version Management

## VERSION files

| File | Controls |
|------|----------|
| `ansible/k3s/VERSION` | VM / guest image release line |
| `src/sek8s/VERSION` | `sek8s` Python package version |
| `src/sek8s-common/VERSION` | `sek8s-common` Python package version |
| `src/attestation-proxy/VERSION` | `attestation-proxy` Python package version |

Each `src/<pkg>/VERSION` is the **source of truth** for `[tool.poetry] version` in the
corresponding `pyproject.toml`. The sync script (`scripts/sync_pyproject_versions.py`)
keeps them aligned, and CI enforces it.

## Version domains — which changes require which bump

There are two independent release domains. A single PR may touch both.

### VM image domain

Changes under any of these paths require an **`ansible/k3s/VERSION`** bump:

- `ansible/*`
- `src/sek8s/*`
- `src/sek8s-common/*`
- `nvevidence/*`
- `pyproject.toml` (root)
- `poetry.lock`

Rationale: all of these are baked into the guest VM image. `sek8s-common` is included
because it is installed on the VM alongside `sek8s`.

### Attestation-proxy domain

Changes under `src/attestation-proxy/*` require an **`src/attestation-proxy/VERSION`**
bump (not `ansible/k3s/VERSION`).

Rationale: the proxy runs as a standalone k3s container image, independently releasable
from the VM image.

### Cross-domain changes

If a PR touches both domains (e.g. `src/sek8s-common/*` and `src/attestation-proxy/*`),
both VERSION files must be bumped.

### `sek8s-common` and proxy consumers

When `sek8s-common` changes, a `ansible/k3s/VERSION` bump is required (VM domain),
but an `attestation-proxy/VERSION` bump is **not** automatically required. The proxy
picks up common changes on its next release. If a common change is breaking for the
proxy, bump the proxy version in the same PR.

## Git tags

Tags are created automatically on merge to `main` when the corresponding VERSION file
changes:

| VERSION file | Tag format |
|-------------|------------|
| `ansible/k3s/VERSION` | `v{version}` |
| `src/sek8s/VERSION` | `sek8s-v{version}` |
| `src/sek8s-common/VERSION` | `sek8s-common-v{version}` |
| `src/attestation-proxy/VERSION` | `attestation-proxy-v{version}` |

The `v{version}` tag (from `ansible/k3s/VERSION`) is the **VM image release tag**.
Per-package tags track Python package versions independently.

## Changelogs

Changelogs live under a top-level `changelogs/` directory, one subdirectory per
component. The format follows [Keep a Changelog](https://keepachangelog.com/).

| Changelog | Tracks | Paired VERSION file |
|-----------|--------|---------------------|
| `changelogs/vm/CHANGELOG.md` | VM / guest image releases | `ansible/k3s/VERSION` |
| `changelogs/sek8s/CHANGELOG.md` | `sek8s` package changes | `src/sek8s/VERSION` |
| `changelogs/attestation-proxy/CHANGELOG.md` | Proxy package changes | `src/attestation-proxy/VERSION` |

`sek8s-common` does not have its own changelog. Common changes are documented in the
consuming package's changelog (`sek8s` or `attestation-proxy`).

### Workflow

When bumping a VERSION file, add a `## [x.y.z]` heading (with date) and entries in the
corresponding changelog. Use the standard categories: Added, Changed, Fixed, Removed.

For **release branches** where multiple features accumulate before merging to main:
add entries under `## [Unreleased]` on the feature branches, then rename the section
to `## [x.y.z] - YYYY-MM-DD` in the release PR that bumps VERSION.

CI enforces: when a VERSION file is bumped, the corresponding changelog must contain
a `## [x.y.z]` heading matching that version.

## CI enforcement

The `version-tag.yml` workflow enforces three things on every PR to `main`:

1. **Domain version check** — if files in a domain changed, the domain's VERSION must
   be bumped.
2. **Pyproject sync check** — `scripts/sync_pyproject_versions.py --check` verifies
   that every `src/<pkg>/VERSION` matches its `pyproject.toml` version.
3. **Changelog check** — if a VERSION file was bumped, the paired CHANGELOG.md must
   contain a `## [x.y.z]` heading for that version.

## Sync script

```bash
# Fix mode: update pyproject.toml files to match VERSION files
python scripts/sync_pyproject_versions.py

# Check mode: exit 1 on mismatch (used in CI)
python scripts/sync_pyproject_versions.py --check
```
