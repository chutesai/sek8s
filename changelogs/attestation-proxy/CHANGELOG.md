# Changelog

All notable changes to the `attestation-proxy` package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

Version source of truth: `src/attestation-proxy/VERSION`

## [0.3.1] - 2026-05-26

### Fixed
- Strip upstream `Server` response header in `proxy_request` so Uvicorn's own header is the only one sent to clients. Forwarding the backend's `Server` header alongside Uvicorn's own produced a duplicate that aiohttp 3.13.4+ rejects with `Duplicate 'Server' header found`.

## [0.3.0] - 2026-05-19

### Fixed
- Added `curl` to the production Docker image so Kubernetes startup, liveness, and readiness exec probes can execute successfully.

> **Note:** The changes listed under [0.2.0] were not published due to a build issue caused by a version misalignment from the prior monorepo restructure. The 0.2.0 changes (X-Signature response header) are first published in this release.

## [0.2.0] - 2026-05-04

### Added
- X-Signature response header on all externally proxied responses. The header contains a base64-encoded RSA-PKCS1v15-SHA256 signature of the response body, signed with the host TLS private key, enabling clients to verify the responder holds the private key corresponding to the TDX-attested certificate.

## [0.1.1] - 2026-04-07

### Changed
- Refactored sek8s to pull out attestaton proxy source code into standalone package to align with version management for the associated image.

## [0.1.0] - 2026-04-02

### Added
- Initial release: extracted from `sek8s` as a standalone package during monorepo
  refactor (Phase 2). Runs as a k3s container image, depends only on `sek8s-common`.

> **Prior history:** Before extraction, proxy code lived in `sek8s`. Notable
> pre-extraction fix: v0.2.3 (2026-03-11) resolved a bug preventing proxy restart
> in the attestation-system namespace without a full VM restart.
