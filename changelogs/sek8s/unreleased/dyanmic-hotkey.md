### Changed

- `AdmissionConfig.chutes_cosign_public_key_path` (single key) replaced with two separate fields: `chutes_public_key_path` (env `CHUTES_PUBLIC_KEY_PATH`, default `/etc/admission-controller/cosign/chutes.pub`) for localregistry-signed images, and `dockerhub_public_key_path` (env `DOCKERHUB_PUBLIC_KEY_PATH`, default `/etc/admission-controller/cosign/dockerhub.pub`) for Docker Hub-signed images.
- `ValidationContext.required_key_path: Optional[Path]` replaced with `required_key_paths: Set[Path]` — the chutes namespace now accepts images signed by either the localregistry key or the Docker Hub key, eliminating false rejections when images are dual-signed or sourced from different registries.
- `ImageConfig.image_pull_allowed_registries` default changed from `["localhost:30500", "127.0.0.1:30500"]` to `["localregistry.chutes.ai:30500"]` to match the static registry hostname decoupled from the validator hotkey.
- `resolve_to_full_ref` registry-matching predicate updated from `.localregistry.chutes.ai` (dot-prefix, validator-scoped) to `localregistry.chutes.ai` (bare hostname) to reflect the static registry change.
