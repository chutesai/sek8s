# TDX VM Host Setup Guide

This guide covers setting up a baremetal host to launch TDX-enabled VMs with GPU passthrough.

> Need the full workflow (host prep → VM launch → deploying the Chutes miner)? Start with [`docs/end-to-end-miner.md`](../docs/end-to-end-miner.md) for an end-to-end view, then return here for host-specific details.

## Prerequisites

- **Hardware**: Intel TDX-capable CPU and NVIDIA GPUs. See [Validated host topologies](#validated-host-topologies).
- **OS**: Ubuntu **25.10** (validated). A 26.04 profile exists but has not yet been end-to-end validated. Ubuntu 25.04 is EOL — use `upgrade-host.yml` to advance to 25.10 first.
- **Access**: Root/sudo privileges on the host; SSH access from the Ansible control machine.

### Validated host topologies

**Validated** means end-to-end tested (TDX host + VM + GPU passthrough). A profile existing in `setup-tdx-host` does **not** imply validation.

| Ubuntu | GPU SKU      | GPU count | Status              | Notes |
|--------|--------------|-----------|---------------------|-------|
| 25.10  | B200         | 8         | Validated           | Host-side Fabric Manager. CX7 NVSwitch bridge PFs stay on host. See [Blackwell HGX notes](#blackwell-hgx-notes). |
| 26.04  | B300         | 8         | Validated           | Same Blackwell HGX architecture as B200. See [Blackwell HGX notes](#blackwell-hgx-notes). |
| 25.10  | RTX Pro 6000 | 8         | Validated           | No NVSwitch. Intel DCAP attestation. |
| 25.10  | H200         | 8         | Validated           | NVSwitch required. Intel DCAP attestation. |
| 25.04  | —            | —         | EOL                 | No profile. Run `upgrade-host.yml` to advance to 25.10. |

Print the canonical matrix from the repo:
```bash
cd host-tools/scripts
./setup-tdx-host --topology-matrix
```

#### Blackwell HGX notes (B200 / B300)

B200 and B300 use a different NVSwitch architecture from H100/H200. Key differences that affect host setup:

- **Host-side Fabric Manager**: NVSwitches are not PCIe devices visible to the guest. `nvidia-fabricmanager` and `nvlsm` run on the *host* and are installed automatically by `setup-tdx-host` when B200 or B300 GPUs are detected. The guest's Fabric Manager is masked.
- **CX7 NVSwitch bridge PFs stay on the host**: ConnectX-7 devices acting as the host interface to NVSwitches (identified by `SMDL=SW_MNG` in PCIe VPD) are excluded from VFIO passthrough. Regular CX7 NIC PFs are still passed through normally.
- **Encrypted NVLink (MPT CC mode)**: NVLink traffic between GPUs and the host Fabric Manager is encrypted, so host-side FM does not compromise the zero-trust security model.
- **`nvidia-open` driver**: Required on both host and guest for Blackwell (already the default in the guest image).

---

## Setup via Ansible (Recommended)

The `ansible/host/` playbooks are the primary way to provision and operate hosts. They handle all setup steps end-to-end, including PCCS configuration, and are idempotent.

### Step 1: Provision the host

```bash
cd ansible/host

# Full host setup (TDX kernel, attestation packages, PCCS, chutes dirs):
ansible-playbook -i ~/chutes/my-inventory.yml playbooks/setup.yml \
  -e pccs_api_key=<your-key> -e pccs_password=<your-password>
```

`pccs_api_key` and `pccs_password` must both be set (or both omitted — see Step 2). Obtain your Intel API key from the [Intel Trusted Services portal](https://api.portal.trustedservices.intel.com/).

A reboot is triggered automatically if a new kernel was installed.

### Step 2: Launch the VM

```bash
ansible-playbook -i ~/chutes/my-inventory.yml playbooks/launch.yml
```

This renders `config.yaml` on the host, downloads the base image if missing, verifies its checksum, and launches the VM via `quick-launch.sh`.

### Subsequent updates

```bash
# Update guest image and relaunch:
ansible-playbook -i ~/chutes/my-inventory.yml playbooks/upgrade-guest.yml

# Upgrade host OS (e.g. 25.10 → 26.04 when validated):
ansible-playbook -i ~/chutes/my-inventory.yml playbooks/upgrade-host.yml
```

See `ansible/host/README.md` for the full playbook reference including `remediate-host.yml` (fix broken hosts), `shutdown.yml`, and `firewall.yml`.

---

## Manual Setup (Advanced / Direct Script Use)

Use these steps if you are not using Ansible or are working directly on the host.

### Step 1: Install TDX host prerequisites

```bash
git clone https://github.com/chutesai/sek8s.git
cd sek8s/host-tools/scripts
sudo ./setup-tdx-host
sudo reboot
```

After reboot, verify TDX is active:
```bash
dmesg | grep -i tdx
# Expected: virt/tdx: module initialized
```

### Step 2: Configure PCCS

PCCS is the local Intel attestation cache. Configure it with your Intel API key and a password:

```bash
pccs-configure
# Enter your API key and a password when prompted — this runs npm install and
# generates the TLS cert/key under /opt/intel/sgx-dcap-pccs/ssl_key/

systemctl restart pccs

sudo PCKIDRetrievalTool \
  -url https://localhost:8081 \
  -use_secure_cert false
# Expected: "the data has been sent to cache server successfully"
```

Obtain your Intel API key from [api.portal.trustedservices.intel.com](https://api.portal.trustedservices.intel.com/).

**Note:** If PCCS was installed non-interactively (e.g. via `setup-tdx-host --noninteractive`) and the service fails with `Cannot find package 'config'`, run:
```bash
cd /opt/intel/sgx-dcap-pccs && npm install
systemctl restart pccs
```

### Step 3: Download the VM image

```bash
cd host-tools/scripts
./quick-launch.sh --download
```

Images are saved to `/var/lib/chutes/base-images/`.

### Step 4: Create configuration file

```bash
./quick-launch.sh --template
# Edit config.yaml with your settings
```

Key fields:
- `miner.ss58` / `miner.seed` — substrate credentials
- `network.public_interface` — host NIC name (e.g. `ens9f0np0`)
- `docker_hub` — optional read-only PAT to avoid anonymous Hub rate limits

See [`scripts/config/CONFIG-GUIDE.md`](scripts/config/CONFIG-GUIDE.md) for the full schema reference.

### Step 5: Launch the VM

```bash
./quick-launch.sh config.yaml
```

The script validates TDX, prepares volumes, configures networking, binds GPUs to `vfio-pci`, and starts the VM.

---

## Management

### VM status and logs
```bash
cat /tmp/tdx-td-pid.pid && ps -p $(cat /tmp/tdx-td-pid.pid)
tail -f /tmp/tdx-guest-td.log
cat /tmp/qemu.log
```

### Stop and clean up
```bash
./quick-launch.sh --clean
```
Removes the VM process, bridge, TAP interfaces, and NAT rules. Volume files are preserved.

### GPU management
```bash
# Query GPU CC/PPCIe mode
sudo nvidia-gpu-tools --query-cc-mode

# Secondary Bus Reset (all GPUs — stop VM first)
chutes-reset-gpus

# Recover a broken GPU
sudo nvidia-gpu-tools --recover-broken-gpu --gpu-bdf=<bdf>
```

Refresh host dependencies on an existing machine (no full re-setup needed):
```bash
sudo ./setup-tdx-host --install-tools-only
```

---

## Troubleshooting

**PCCS fails with `ERR_MODULE_NOT_FOUND` / `Cannot find package 'config'`**
```bash
cd /opt/intel/sgx-dcap-pccs && npm install && systemctl restart pccs
```
Caused by non-interactive install skipping the `npm install` post-install step.

**GPU stuck or unhealthy**
```bash
chutes-reset-gpus                                          # all GPUs
sudo nvidia-gpu-tools --reset-with-sbr --gpu-bdf=<bdf>    # single GPU
```

**TDX not initialized after reboot**
```bash
dmesg | grep -i tdx
# If blank: verify GRUB entry via `grub-editenv list`; re-run setup-tdx-host if needed
```

**Network not accessible**
```bash
ip link show br0 && ip link show vmtap0
sudo sysctl net.ipv4.ip_forward       # should be 1
```

---

## File Locations

| Path | Description |
|------|-------------|
| `/var/lib/chutes/base-images/tdx-guest.qcow2` | Base VM image |
| `/var/lib/chutes/vm-overlays/tdx-<hostname>-<sha>.qcow2` | Per-launch overlay |
| `host-tools/scripts/cache-<hostname>.raw` | HF/model cache volume (XFS) |
| `host-tools/scripts/storage-<hostname>.raw` | k3s/containerd/kubelet volume |
| `host-tools/scripts/config-<hostname>.qcow2` | Credentials config volume |
| `/tmp/tdx-guest-td.log` | VM serial console log |
| `/tmp/qemu.log` | QEMU debug log |
| `/tmp/tdx-td-pid.pid` | VM process PID |

---

## Additional Documentation

- [Ansible Host Playbooks](../ansible/host/README.md) — full playbook reference
- [Configuration Guide](scripts/config/CONFIG-GUIDE.md) — full config schema
- [Intel TDX Documentation](https://www.intel.com/content/www/us/en/developer/tools/trust-domain-extensions/overview.html)
