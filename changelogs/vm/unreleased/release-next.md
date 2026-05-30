### Fixed

- `nvidia-fabricmanager` is no longer reported as unhealthy when it is intentionally masked (valid on non-NVLink hosts). The services overview now returns `ok` in this configuration instead of incorrectly reporting `degraded`.
