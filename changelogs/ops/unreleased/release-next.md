### Added

- `make publish-base-image` uploads a built base image and its `.sha256`/`.provenance` sidecars to
  R2 under `base-images/<version>/`. Publishing is a separate step from building, so a build cannot
  accidentally publish and credentials stay out of the build path. It refuses to overwrite an
  existing published version: consumers pin a version and a hash together, so different bytes under
  the same version fail their checksum mid-build rather than at publish time.
- A `guest-base-image` CI job requires a base image bump when the `common` package set changes.
  The guest build no longer runs that role — those packages come from the published base image built
  by `playbooks/base-image.yml` — so a package added without rebuilding the layer is simply absent
  from the image, and nothing at build time notices: the layer's hash still matches and the build
  still succeeds. This is the one drift the hash pinning cannot catch by itself. Fails a PR that
  touches `ansible/guest/roles/common/` without updating
  `ansible/guest/BASE_IMAGE_VERSION`.
  It also enforces that `BASE_IMAGE_VERSION` and `BASE_IMAGE_SHA256` move together: the version
  selects the artifact and the hash validates it, so changing one alone is a broken pin — and
  republishing different bytes under an existing version would silently change what consumers who
  already pinned it receive.
