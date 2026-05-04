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
