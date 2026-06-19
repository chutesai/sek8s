### Added
- Presigned-URL credential redaction in model-download error logs (`_redact_urls`): URL query strings (e.g. `?X-Amz-Signature=…`) are replaced with `?<redacted>` before exception text is written to stderr/the journal, so short-lived CDN credentials never get logged.

### Changed
- Model-download retry classifier now keys on typed exceptions instead of substring matching. Replaced the old `"403"/"Forbidden" in str(exc)` check in `download.py` with `_is_transient_download_error`, which retries only genuine transient download-layer failures (CDN presigned-URL expiry → HTTP 403 on the file GET, and XET transport hiccups) and immediately raises hard auth/availability errors (gated repo, bad/expired token → 401, missing repo/revision → 404, XET auth). This avoids burning 5 retries (~150s) on a permanent failure and avoids treating any message that merely contains "403" as transient.
- Removed internal audit finding-ID references (`SEK8S-NNN`) from public-bound source comments/docstrings; the finding↔test mapping now lives only in the sensitive audit doc, enforced by a new leak-guard test.
