### Added

- `WebServer.serve()` (async) in `sek8s-common`, alongside `run()` (blocking).
  Both derive their uvicorn arguments from a single `_uvicorn_kwargs()` source of
  truth, so every server honours its full TLS/mTLS/bind config regardless of how
  it is hosted (single-server process via `run()`, or several servers sharing one
  event loop via `serve()`).

### Changed

- Registry defaults moved from `localregistry.chutes.ai:30500` to
  `registry.chutes.ai`: `ImageConfig.image_pull_allowed_registries`,
  `AdmissionConfig.allowed_registries`, and the `chutes_public_key_path`
  description in `config.py`.
- `resolve_to_full_ref` (`system_manager/images/util.py`) resolves short-form
  image refs against `registry.chutes.ai` and drops the now-unused
  `localhost` / `127.0.0.1` special-casing for full-ref detection.
