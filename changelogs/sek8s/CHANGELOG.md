# Changelog

All notable changes to the `sek8s` package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

Version source of truth: `src/sek8s/VERSION`

> **Note:** Prior to 0.2.5, the sek8s package and VM image shared a single version
> and codebase. Entries below 0.2.5 reflect service-level changes from that era.

## [0.3.0] - 2026-05-04

### Added
- `POST /cache/{chute_id}/cancel` endpoint to cancel an in-progress HuggingFace model download, with an optional `cleanup` query parameter to delete partial files from disk after cancelling.
- `POST /cache/purge` endpoint to purge stale HuggingFace revisions from all tracked chutes without evicting any chutes. Returns `purged_bytes` freed. Useful for reclaiming orphaned blobs on a schedule independently from age/size-based cleanup.
- Stale HF revision purging during cache cleanup: after removing chutes that exceed age/size limits, surviving chutes have orphaned revisions pruned via the HuggingFace `delete_revisions` API. Bytes freed by purging are reported separately as `purged_bytes` in the cleanup response.
- `DownloadProcess` class (`cache/download.py`) isolates each model download in a subprocess (`-m sek8s.system_manager.cache.download`), providing clean state properties (`is_running`, `is_done`, `succeeded`, `was_cancelled`, `error`) and SIGTERM-based cancellation without leaking asyncio tasks into the manager.
- `chmod_tree` utility for recursively setting permissions on a cache directory tree (used by the download subprocess pipeline).

### Changed
- `HuggingFaceSnapshot` download state is now tracked via `DownloadProcess` instead of a raw `asyncio.Task`, decoupling download lifecycle management from the manager.
- `CacheCleanupResponse` now includes a `purged_bytes` field alongside `freed_bytes` and `removed_chutes`.
- `CACHE_COMPLETE_MARKER`, `CACHE_STALE_MARKER`, and `chmod_if_owned` moved from `manager.py` to `util.py` to support the subprocess entry point without circular imports.

## [0.2.6] - 2026-04-07

### Changed
- Refactored sek8s module to contain only necessary code for guest services.

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
