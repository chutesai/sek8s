### Changed

- Install a loguru sink with `diagnose=False` at startup
  (`sek8s_common.log_config.configure_logging`), so exception tracebacks no longer render frame-local
  values into the proxy's logs. The tracebacks themselves are unchanged.
