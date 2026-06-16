### Removed
- Deleted the unused `chutes-gpu/templates/monitoring-values.yaml.j2` Helm values template. It was never referenced by any task and described a chart structure the guest does not deploy.

### Fixed
- Guest monitoring now exposes the in-VM Prometheus server on `NodePort` 30090 (`server.service.type=NodePort`, `server.service.nodePort=30090`) instead of the chart-default `ClusterIP`. The control-plane `chutes-monitoring` federating Prometheus scrapes each TEE VM at `<vm-ip>:30090/federate`, which requires the endpoint to be reachable from outside the guest cluster. The guest UFW rule for 30090 and the host NodePort range (30000–32767) were already in place; only the service type was missing.
