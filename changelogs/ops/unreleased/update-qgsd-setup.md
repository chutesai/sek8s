### Fixed
- `setup-tdx-host` now configures QGS for vsock mode (`port = 4050` in `/etc/qgs.conf`) on all hosts; the Intel-shipped default leaves this commented out, silently breaking TDX quote generation in VMs
- `setup-tdx-host` now sets `use_secure_cert: false` in `/etc/sgx_default_qcnl.conf` so QGS can reach the local PCCS instance which uses a self-signed certificate
