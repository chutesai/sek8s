### Changed

- Deployment manifest updated: `secret-reader` RBAC Role now includes `validator-auth` in `resourceNames`, and the `wait-for-credentials` init container waits for the `validator-auth` Secret before the attestation-proxy pod starts. The `validator-auth` Secret is no longer baked into the proxy manifest at build time — it is created at runtime by the cluster-init script on every boot.
