### Changed
- Split cosign signature verification into two keys: `chutes.pub` for the private localregistry (and wildcard fallback), `dockerhub.pub` for Docker Hub `parachutes/*` images
- `AdmissionConfig`: replaced `chutes_cosign_public_key_path` (`CHUTES_COSIGN_PUBLIC_KEY_PATH`) with `chutes_public_key_path` (`CHUTES_PUBLIC_KEY_PATH`) and new `dockerhub_public_key_path` (`DOCKERHUB_PUBLIC_KEY_PATH`)
- `ValidationContext.required_key_path: Optional[Path]` replaced by `required_key_paths: set[Path]`; `_require_ctx_key` now validates against set membership rather than a single path
