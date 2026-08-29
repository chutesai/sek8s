### Added

- System status `/services` allowlist now covers `chute-log-shipper` and `opa`. The prod VM has no console
  or SSH access, so an unlisted unit cannot be status-checked or log-tailed by the miner CLI at all.

### Changed

- Guest services now install a loguru sink with `diagnose=False`
  (`sek8s_common.log_config.configure_logging`, called from each service entrypoint). Loguru's default
  renders frame-local *values* into exception tracebacks, which on an error path could write request data
  into a journal the miner can read over the status API. Tracebacks are otherwise unchanged — `backtrace`
  stays on, so every frame, line, and source line is still logged.
