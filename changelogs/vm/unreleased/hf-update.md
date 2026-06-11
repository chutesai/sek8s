### Changed
- System manager env: replaced `HF_HUB_DISABLE_XET=1` + `HF_HUB_ENABLE_HF_TRANSFER=1` with throttled XET tuning (`HF_XET_FIXED_DOWNLOAD_CONCURRENCY=16`, `TOKIO_WORKER_THREADS=8`)
- OPA admission policy: added `HF_XET_FIXED_DOWNLOAD_CONCURRENCY` and `TOKIO_WORKER_THREADS` to allowed pod env vars
