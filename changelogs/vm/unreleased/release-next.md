### Changed

- Attestation proxy init container migrated from `bitnami/kubectl:latest` (unsigned, unpinned) to `parachutes/kubectl` (cosign-signed with `dockerhub.pub`). Removed the `require_signature: false` exception for `bitnami/kubectl` from the cosign registry config and removed `bitnami` from the OPA registry allowlist.