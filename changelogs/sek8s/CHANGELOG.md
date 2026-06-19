# Changelog

All notable changes to the `sek8s` package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

Version source of truth: `src/sek8s/VERSION`

> **Note:** Prior to 0.2.5, the sek8s package and VM image shared a single version
> and codebase. Entries below 0.2.5 reflect service-level changes from that era.

## [0.3.1] - 2026-06-19

### Added
- Retry logic in `download.py` for transient 403 errors caused by presigned CDN URL expiration on large model downloads (up to 5 retries with exponential backoff)
- Presigned-URL credential redaction in model-download error logs (`_redact_urls`): URL query strings (e.g. `?X-Amz-Signature=…`) are replaced with `?<redacted>` before exception text is written to stderr/the journal, so short-lived CDN credentials never get logged.

### Changed
- Upgraded `huggingface_hub` from 0.36.2 to ^1.18.0; removed deprecated `hf-transfer` dependency
- System manager downloads now use throttled XET (`HF_XET_FIXED_DOWNLOAD_CONCURRENCY=16`, `TOKIO_WORKER_THREADS=8`) instead of disabled XET with httpx fallback — benchmarked at ~500 MB/s vs ~22 MB/s in TDX
- Model-download retry classifier now keys on typed exceptions instead of substring matching. Replaced the old `"403"/"Forbidden" in str(exc)` check in `download.py` with `_is_transient_download_error`, which retries only genuine transient download-layer failures (CDN presigned-URL expiry → HTTP 403 on the file GET, and XET transport hiccups) and immediately raises hard auth/availability errors (gated repo, bad/expired token → 401, missing repo/revision → 404, XET auth). This avoids burning 5 retries (~150s) on a permanent failure and avoids treating any message that merely contains "403" as transient.
- Removed internal audit finding-ID references (`SEK8S-NNN`) from public-bound source comments/docstrings; the finding↔test mapping now lives only in the sensitive audit doc, enforced by a new leak-guard test.

## [0.3.0] - 2026-05-15

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

### Fixed
- Mutating webhook no longer applies `automountServiceAccountToken: false` on Pod UPDATE operations, preventing the API server from rejecting immutable-field mutations (e.g. Job controller finalizer sync on completed CronJob pods).
- OPA validating policy (`chutes.rego`) no longer enforces pod-spec rules on Pod UPDATE operations; pod specs are immutable after creation, so spec checks on UPDATE blocked finalizer removal and pod cleanup for pods created before the SA token policy was deployed.

### Removed
- **Image pull endpoints**: Removed `POST /images/pull` and `GET /images/pull/status`
  from the system manager images API. These endpoints are no longer part of the
  supported image management interface.

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
