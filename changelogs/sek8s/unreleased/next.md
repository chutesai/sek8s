### Fixed

- Status endpoints answered `200 OK` with empty results when the subprocess behind them
  failed, making a broken privileged path indistinguishable from a healthy-but-empty VM:
  `/status/disk/space` reported `total_size_bytes: 0` when `du` never ran, and
  `/status/services/{id}/logs` reported zero entries when `journalctl` failed.

### Changed

- `run_command` now fails closed: a non-zero exit raises `500 command_failed` carrying the
  command, exit code and first line of stderr. Callers where a non-zero exit is meaningful
  opt out with `check=False` and say why — `systemctl show` (a reportable service state
  that the overview renders as degraded), `df` (a supplementary field that degrades to
  null), `nvidia-smi` (reported rather than consumed, with `status`/`exit_code` in the
  body), and `du` (which exits 1 for any unreadable subtree while still reporting the
  rest, so it is gated on having produced output at all).
- Cache size accounting dropped partial downloads. `scan_cache_dir` counts only blobs that
  already have a snapshot symlink — which HuggingFace creates when a file finishes — so
  `.incomplete` blobs were invisible to it, and `du` was consulted only while a download was
  actively running. A cancelled or crashed download therefore reported megabytes while holding
  tens of gigabytes (measured: 18 MB reported against 14.9 GiB on disk), and `cleanup()`'s
  max-size eviction cannot reclaim space it cannot see. Every state except `PRESENT` — the one
  state with no partials, since the complete marker is written last — is now sized with `du`.
