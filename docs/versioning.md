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

Changelogs use a **fragment-based** system. Each feature branch drops a `.md` file
into `changelogs/<component>/unreleased/`. On merge to main, automation aggregates
the fragments into a versioned entry in `CHANGELOG.md` and deletes the fragment files.

| Component | CHANGELOG | Fragments | Paired VERSION file |
|-----------|-----------|-----------|---------------------|
| VM image | `changelogs/vm/CHANGELOG.md` | `changelogs/vm/unreleased/` | `ansible/k3s/VERSION` |
| sek8s | `changelogs/sek8s/CHANGELOG.md` | `changelogs/sek8s/unreleased/` | `src/sek8s/VERSION` |
| Proxy | `changelogs/attestation-proxy/CHANGELOG.md` | `changelogs/attestation-proxy/unreleased/` | `src/attestation-proxy/VERSION` |

`sek8s-common` does not have its own changelog. Common changes are documented in the
consuming package's changelog (`sek8s` or `attestation-proxy`).

### Adding a changelog fragment

On your feature branch, create a `.md` file in the appropriate `unreleased/` directory.
The filename should match the branch name (strip the prefix):
`feature/nvidia-590-drivers` -> `nvidia-590-drivers.md`.

Use [Keep a Changelog](https://keepachangelog.com/) category headers:

```markdown
### Added
- New image management API endpoints

### Fixed
- Attestation-proxy restart bug in attestation-system namespace
```

Categories: **Added**, **Changed**, **Fixed**, **Removed**.

### What happens on merge to main

The `version-tag.yml` workflow automatically:

1. Collects all `.md` fragments from each component's `unreleased/` directory.
2. Groups entries by category (Added > Changed > Fixed > Removed).
3. Writes a `## [x.y.z] - YYYY-MM-DD` section to `CHANGELOG.md`.
4. Deletes the fragment files and commits the result.
5. Creates per-package git tags.

Never manually add `## [x.y.z]` headings to `CHANGELOG.md` -- automation owns those.

## CI enforcement

The `version-tag.yml` workflow enforces on every PR to `main`:

1. **Domain version check** — if files in a domain changed, the domain's VERSION must
   be bumped.
2. **Pyproject sync check** — `scripts/sync_pyproject_versions.py --check` verifies
   that every `src/<pkg>/VERSION` matches its `pyproject.toml` version.
3. **Changelog fragment check** — if a VERSION file was bumped, the paired
   `unreleased/` directory must contain at least one `.md` fragment, and the versioned
   heading must NOT already exist in `CHANGELOG.md`.

## Scripts

```bash
# Sync VERSION -> pyproject.toml (fix mode)
python scripts/sync_pyproject_versions.py

# Sync check (CI)
python scripts/sync_pyproject_versions.py --check

# Validate changelog fragments exist for bumped versions (CI)
python scripts/promote_changelogs.py --check --version-files ansible/k3s/VERSION

# Promote fragments into CHANGELOG.md (CI, on merge to main)
python scripts/promote_changelogs.py --promote --version-files ansible/k3s/VERSION
```
