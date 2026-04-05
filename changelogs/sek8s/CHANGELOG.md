# Changelog

All notable changes to the `sek8s` package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

Version source of truth: `src/sek8s/VERSION`

> **Note:** Prior to 0.2.5, the sek8s package and VM image shared a single version
> and codebase. Entries below 0.2.5 reflect service-level changes from that era.

## [Unreleased]

## [0.2.5] - 2026-04-02

### Changed
- Initial release under `src/sek8s/` layout (monorepo package refactor).
- Shared code extracted to `sek8s-common`; `sek8s` depends on `sek8s-common`.

## [0.2.3] - 2026-03-11

### Added
- Image management API in system manager: pull, list, delete, prune images from
  the validator mirror.

### Fixed
- Attestation-proxy restart bug in the attestation-system namespace (now handled
  via kubectl without requiring VM restart).

## [0.2.2] - 2026-03-06

### Changed
- System manager API updated: improved cache download performance, concurrent
  download resource handling.
- Cache cleaner updated to check GPU processes and VRAM threshold before eviction.

### Fixed
- Fixed 500 errors from resource constraints during concurrent model downloads.
