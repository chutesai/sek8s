### Changed
- Upgraded `huggingface_hub` from 0.36.2 to ^1.18.0; removed deprecated `hf-transfer` dependency
- System manager downloads now use throttled XET (`HF_XET_FIXED_DOWNLOAD_CONCURRENCY=16`, `TOKIO_WORKER_THREADS=8`) instead of disabled XET with httpx fallback — benchmarked at ~500 MB/s vs ~22 MB/s in TDX

### Added
- Retry logic in `download.py` for transient 403 errors caused by presigned CDN URL expiration on large model downloads (up to 5 retries with exponential backoff)
