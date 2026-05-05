### Changed

- k3s upgraded from `v1.33.7+k3s1` to `v1.35.4+k3s1`
- CUDA toolkit upgraded from `13-0` to `13-2`
- NVIDIA driver package upgraded from `595.58.03-1ubuntu1` to `595.71.05-1ubuntu1`
- GPU Operator Helm chart upgraded from `v24.9.2` to `v26.3.1`; build-time install now uses `operator.upgradeCRD=true`
- Helm CLI upgraded from `v3.11.3` to `v3.20.2`
- OPA upgraded from `0.68.0` to `1.15.2` (0.x to 1.x major bump; existing policy tests confirmed passing)
- cosign pinned to `v2.6.3` (previously fetched `latest` at build time, non-deterministic; fixes CVE-2026-39395)
- `nv-attestation-sdk` constraint bumped from `^2.6.2` to `^2.7.0` in `nvevidence/`

### Added

- Boot-time Helm upgrade script (`04-helm-chart-upgrade.sh`) refactored into a generic multi-chart dispatcher; per-chart configs in `/etc/chutes/chart-configs/` and optional override scripts in `/etc/chutes/chart-upgrade-overrides/` support custom upgrade logic (e.g. GPU Operator CRD migration)
- GPU Operator boot-time upgrade override script handles CRD migration with `--disable-openapi-validation` and `operator.upgradeCRD=true` for persistent clusters upgrading across major chart versions
