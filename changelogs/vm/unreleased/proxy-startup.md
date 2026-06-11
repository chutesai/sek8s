### Fixed
- System manager no longer crash-loops waiting for k3s: made `ReadOnlyPaths=/run/k3s/containerd` optional so the service starts immediately on boot even if k3s hasn't created the socket yet
- Attestation proxy startup probe: increased tolerance from 65s to 310s as a safety net for slow boots
- k3s boot ordering: added `After=attestation-service.service` so the attestation-proxy pod isn't scheduled before the host attestation socket and TLS certs exist
