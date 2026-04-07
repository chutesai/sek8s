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
into `changelogs/<component>/unreleased/`. Promotion aggregates fragments into a
versioned `## [x.y.z]` entry in `CHANGELOG.md` and deletes the fragment files.

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

### Promotion: how fragments become changelog entries

Promotion is **idempotent** — running it multiple times is safe. If the version heading
already exists, new fragments are merged into the existing section by category.

#### Release branch workflow (`release/**`)

1. Feature branches merge into `release/next` (or similar).
2. On each push to a `release/**` branch, the `changelog-auto-promote.yml` workflow
   runs `promote_changelogs.py --promote`, commits promoted changelogs, and pushes
   directly to the release branch.
3. When the release branch is PR'd to `main`, the strict check enforces that **no
   fragments remain** — everything must already be promoted.
4. On merge to `main`, tags are created for bumped versions.

#### Trunk-based workflow (direct to main)

1. Before creating a PR to `main`, run `make promote-changelogs` locally.
2. The same strict check applies: no fragments allowed on PRs to `main`.

Never manually add `## [x.y.z]` headings to `CHANGELOG.md` — automation owns those.

### Git tags

Tags are created on merge to `main` by the `version-tag.yml` workflow. For components
with changelogs, tags are only created when the version heading exists in `CHANGELOG.md`.
For `sek8s-common` (no changelog), tags are created unconditionally when the VERSION
file changes.

## CI enforcement

The `version-tag.yml` workflow runs on PRs to `main` and `release/**` branches, and
on push to `main`:

1. **Domain version check** — if files in a domain changed, the domain's VERSION must
   be bumped.
2. **Pyproject sync check** — `scripts/sync_pyproject_versions.py --check` verifies
   that every `src/<pkg>/VERSION` matches its `pyproject.toml` version.
3. **Changelog check** — on PRs to `main` and push to `main`: strict mode, fails if
   any fragments remain in any `unreleased/` directory. On PRs to `release/**`:
   normal mode, validates fragments exist for bumped versions.

## Scripts

```bash
# Sync VERSION -> pyproject.toml (fix mode)
python scripts/sync_pyproject_versions.py

# Sync check (CI)
python scripts/sync_pyproject_versions.py --check

# Promote fragments into CHANGELOG.md (idempotent)
make promote-changelogs
# or: python scripts/promote_changelogs.py --promote

# Verify no orphaned fragments (strict, used before merging to main)
make check-changelogs
# or: python scripts/promote_changelogs.py --check --strict

# Validate fragments exist for bumped versions (normal mode, release branches)
python scripts/promote_changelogs.py --check
```
