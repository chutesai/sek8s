# TDX VM Host Setup Guide

This guide walks you through setting up a baremetal host to launch TDX-enabled VMs with GPU passthrough, isolated networking, and secure configuration using a streamlined, automated workflow.

> Need the full workflow (host prep → VM launch → deploying the Chutes miner)? Start with [`docs/end-to-end-miner.md`](../docs/end-to-end-miner.md) for an end-to-end view, then return here for host-specific details.

## Prerequisites

- **Hardware**: Intel TDX-capable CPU and NVIDIA GPUs. **8× H200: NVSwitch required** (validated stack). **RTX Pro 6000** has no NVSwitch. See [Validated host topologies](#validated-host-topologies).
- **OS**: Host profiles exist for Ubuntu **25.04** and **25.10**; **lab-validated** topologies are narrower (see below).

### Validated host topologies

**Host profile support** (what `setup-tdx-host` configures) is per **Ubuntu version**. **Validated** means we have **end-to-end** tested that OS + GPU SKU + GPU count (TDX host, VM, passthrough). Other mixes may work but are not marked validated until someone adds a row in `chutes/host/support_matrix.py`.

| Ubuntu | GPU SKU      | GPU count | Status    | Notes |
|--------|--------------|-----------|-----------|-------|
| 25.04  | H200         | 8         | Validated | NVSwitch required. |
| 25.10  | RTX Pro 6000 | 8         | Validated | No NVSwitch (this SKU). |

Print the canonical matrix from the repo (no sudo):

```bash
cd host-tools/scripts
./setup-tdx-host --topology-matrix
```

- **Access**: Root/sudo privileges
- **Network**: Public network interface (e.g., `ens9f0np0`)
- **Python**: Python 3 with PyYAML (`pip3 install pyyaml`)
- **aria2**: For downloading VM images (`sudo apt install aria2`)

## Architecture Overview

The setup creates this architecture:
```
Internet ←→ Public Interface ←→ Bridge ←→ TAP ←→ TDX VM
                                            ↓
                                      GPU Passthrough (PPCIe Mode)
                                      Config Volume (credentials + Docker Hub auth)
                                      Cache Volume (HF/model caches)
                                      Storage Volume (k3s + containerd + kubelet)
                                      k3s Cluster
```

**Note**: **8× H200** uses **PPCIe** with **NVSwitch** (required for the validated topology). **RTX Pro 6000** uses **CC mode** without NVSwitch.

---

## Quick Start

For those familiar with the setup, here's the complete sequence:
```bash
# 1. Setup TDX host (one-time, auto-detects Ubuntu version)
cd host-tools/scripts
sudo ./setup-tdx-host && sudo reboot

# 2. Configure PCCS
pccs-configure

# 3. Download guest image
cd host-tools/scripts
./quick-launch.sh --download

# 4. Create configuration from template
./quick-launch.sh --template
# Edit config.yaml with your settings

# 5. Launch VM (GPU config, binding, volumes, and networking run automatically)
./quick-launch.sh config.yaml
```

---

## Detailed Setup

### Step 1: Install TDX Host Prerequisites

The host setup script configures the kernel, QEMU, attestation services, and firmware for TDX support. It reads the **running** Ubuntu release from `lsb_release` and selects the matching profile (PPAs, kernel, packages, GRUB config). There is **no** CLI flag to force a different OS version—use the correct Ubuntu install for your hardware (e.g. 25.04 vs 25.10) before running setup.

**Supported OS versions (host profile):**
- **Ubuntu 25.04** — TDX via kobuk-team PPA (typical for H200-class hosts)
- **Ubuntu 25.10** — native TDX kernel (typical for RTX Pro 6000–class hosts)

Which **(OS × GPU × count)** pairs are **lab-validated** is separate; see [Validated host topologies](#validated-host-topologies) or `./setup-tdx-host --topology-matrix`.

```bash
# Clone the repository
git clone https://github.com/chutesai/sek8s.git
cd sek8s

# Run the TDX host setup script (Ubuntu version from lsb_release only)
cd host-tools/scripts
sudo ./setup-tdx-host

# Reboot to load TDX-enabled kernel
sudo reboot
```

**After reboot, verify TDX is available:**
```bash
dmesg | grep -i tdx
# Expected output includes one of:
#   [    x.xxxxx] tdx: TDX module initialized        (older kobuk kernel)
#   [    x.xxxxx] virt/tdx: module initialized        (newer kobuk / upstream kernel)
```

---

### Step 2: Register the Platform

Ensure the platform is registered with Intel according to Intel's [docs](https://cc-enabling.trustedservices.intel.com/intel-tdx-enabling-guide/02/infrastructure_setup/#platform-registration)

Using Indirect Registration as an example
```bash
$ pccs-configure
# Configure PCCS with your API key and a password, otherwise defaults are fine

$ systemctl restart pccs
$ sudo PCKIDRetrievalTool \
    -url https://localhost:8081 \
    -use_secure_cert false

Intel(R) Software Guard Extensions PCK Cert ID Retrieval Tool Version 1.21.100.3

Warning: platform manifest is not available or current platform is not multi-package platform.

 Please input the pccs password, and use "Enter key" to end
the data has been sent to cache server successfully
```

**NOTE**
Obtain your Intel API Key from their portal:
https://api.portal.trustedservices.intel.com/

### Step 3: Download the VM Image

Download the prebuilt VM image using `quick-launch.sh` (requires `aria2`):
```bash
cd host-tools/scripts
./quick-launch.sh --download          # production image
./quick-launch.sh --download-debug    # debug image (SSH enabled, no encryption)
```

Images are saved to `/var/lib/chutes/base-images/`. The production image lands at `/var/lib/chutes/base-images/tdx-guest.qcow2`, which is the default path used by `quick-launch.sh`.

---

### Step 4: Create Configuration File

Navigate to the scripts directory and create your configuration from the template:
```bash
cd host-tools/scripts
./quick-launch.sh --template
```

This creates `config.yaml`. Edit it with your deployment settings:
```yaml
vm:
  hostname: chutes-miner-tee-0
  base_image: ""       # Empty = /var/lib/chutes/base-images/tdx-guest.qcow2
  overlay_directory: "" # Empty = /var/lib/chutes/vm-overlays/

miner:
  ss58: "<your_ss58_address>"
  seed: "<your_seed_no_0x_prefix>"

# Optional: Docker Hub credentials for authenticated pulls (avoids anonymous rate limits)
# docker_hub:
#   username: "your_dockerhub_username"
#   token: "dckr_pat_..."

network:
  vm_ip: "192.168.100.2"
  bridge_ip: "192.168.100.1/24"
  dns: "8.8.8.8"
  public_interface: "ens9f0np0"  # Change to match your hardware
  type: "tap"

volumes:
  cache:
    size: "5000G"
    path: ""  # Empty = cache-<hostname>.raw (auto-created)
  storage:
    size: "500G"
    path: ""  # Empty = storage-<hostname>.raw (auto-created)
  config:
    path: ""  # Empty = config-<hostname>.qcow2 (auto-created)

devices:
  bind_devices: true

runtime:
  foreground: false
```

See [`config/CONFIG-GUIDE.md`](scripts/config/CONFIG-GUIDE.md) for the full schema reference and validation details.

> **Note:** Memory, vCPU count, GPU MMIO, and PCI hole sizing are fixed inside
> `run-td` to preserve RTMR determinism. These canonical values are baked into
> the script and are not configurable.

**Required Configuration:**
- `vm.hostname`: Unique identifier for this miner
- `miner.ss58` / `miner.seed`: Your substrate credentials
- `network.public_interface`: Your host's public network interface name

**Optional Configuration:**
- `docker_hub`: Docker Hub username + read-only PAT for authenticated container pulls and cosign verification. Without it, the VM uses anonymous Hub quota which is often too low.
- `vm.base_image` / `vm.overlay_directory`: Override default image and overlay paths.

**Network Configuration:**
- The IP addresses should match your network topology
- Default gateway will be `bridge_ip` without the subnet mask
- Ensure `vm_ip` and `bridge_ip` are in the same subnet

---

### Step 5: Launch the VM

With your configuration file ready, launch the VM:
```bash
./quick-launch.sh config.yaml
```

The script will automatically:
1. **Validate host configuration** - Checks TDX module is initialized and NUMA zone reclaim is disabled
2. **Prepare cache volume** - Creates `cache-<hostname>.raw` (XFS, for HF/model caches at `/var/snap`)
3. **Prepare storage volume** - Creates `storage-<hostname>.raw` (XFS, for k3s, containerd, and kubelet)
4. **Create config volume** - Packages credentials, network config, and optional Docker Hub auth into `config-<hostname>.qcow2`
5. **Verify base image** - SHA256 checksum of the base qcow2, then creates/reuses a qcow2 overlay
6. **Setup bridge networking** - Configures isolated bridge network with NAT
7. **Launch TDX VM** - `run-td` detects GPUs, configures PPCIe/CC modes, binds devices to `vfio-pci`, and boots the VM

**What happens during launch:**
- Cache volume at `cache-<hostname>.raw` is mounted as `/var/snap` in the guest (HF model caches)
- Storage volume at `storage-<hostname>.raw` holds `/var/lib/rancher/k3s`, `/var/lib/kubelet`, admission controller certs, and chutes agent state
- Config volume at `config-<hostname>.qcow2` is refreshed on each launch with current credentials
- Overlay image at `/var/lib/chutes/vm-overlays/tdx-<hostname>-<sha>.qcow2` preserves the base image
- Bridge network `br0` is configured with TAP interface
- NAT rules are applied for k3s API (6443) and NodePorts (30000-32767)
- VM starts in daemon mode with PID tracking

---

## Management Commands

### Check VM Status
```bash
# quick-launch does not expose --status; check the PID manually
cat /tmp/tdx-td-pid.pid && ps -p $(cat /tmp/tdx-td-pid.pid)

# Check via PID file:
cd host-tools/scripts
cat /tmp/tdx-td-pid.pid && ps -p $(cat /tmp/tdx-td-pid.pid)
```

### View VM Logs
```bash
# Serial console output
cat /tmp/tdx-guest-td.log

# Follow logs in real-time
tail -f /tmp/tdx-guest-td.log

# QEMU debug logs
cat /tmp/qemu.log
```

### Stop and Clean Up Everything
```bash
./quick-launch.sh --clean
```

This removes:
- Running VM process
- Bridge network and TAP interfaces
- iptables NAT rules

**Note**: Volume files (cache, storage, and config) are NOT deleted during cleanup. GPU devices remain in their current state and will be reconfigured automatically on the next launch.

---

## Advanced Usage

### Command Line Overrides

CLI flags override values from `config.yaml`. Common overrides:
```bash
# Run in foreground mode (see all output)
./quick-launch.sh config.yaml --foreground

# Use a custom base image
./quick-launch.sh config.yaml --base-image /path/to/custom-tdx-guest.qcow2

# Override VM IP
./quick-launch.sh config.yaml --vm-ip 192.168.100.5

# Provide Docker Hub credentials via CLI instead of config file
./quick-launch.sh config.yaml --docker-hub-username user --docker-hub-token dckr_pat_xxx
```

For the full list of options, run `./quick-launch.sh --help`. Volume sizes and paths are best managed through `config.yaml` rather than CLI flags.

---

## Verification and Troubleshooting

### Verify Host Configuration
```bash
# Check kernel parameters
cat /proc/cmdline | grep -E 'kvm_intel.tdx'

# Verify TDX module
dmesg | grep -i tdx
```

### Verify GPU Configuration
```bash
# List NVIDIA devices
lspci -nn -d 10de:

# Check VFIO bindings
ls /sys/bus/pci/drivers/vfio-pci/ | grep '^0'

# Query GPU CC/PPCIe mode
sudo nvidia-gpu-tools --query-cc-mode
```

### Verify Network Configuration
```bash
# Check bridge status
ip addr show br0
ip link show vmtap0

# Verify NAT rules
sudo iptables -t nat -L -n -v | grep 192.168.100

# Test connectivity from host
ping -c 3 192.168.100.2
```

### Verify VM Operation
```bash
# Check VM process
cat /tmp/tdx-td-pid.pid && ps -p $(cat /tmp/tdx-td-pid.pid)

# View GPU passthrough in logs
grep -i nvidia /tmp/tdx-guest-td.log

# Check cache volume mount
grep -i "cache\|vdb\|/var/snap" /tmp/tdx-guest-td.log

# Verify k3s cluster is accessible
# (from external machine)
curl -k https://<host_public_ip>:6443
```

### Common Issues

**Issue: "VM fails to start with GPU errors"**

Relaunch the VM -- `run-td` automatically detects, configures, and binds GPUs on each launch:
```bash
./quick-launch.sh config.yaml
```

**Issue: "GPU appears stuck or unhealthy"**

`nvidia-gpu-tools` is bundled and installed automatically by `run-td`. No host NVIDIA driver is needed. After stopping the VM, use `chutes-reset-gpus` for a Secondary Bus Reset on all GPUs. To install or refresh host dependencies on an existing machine, run **`sudo ./setup-tdx-host --install-tools-only`** from `host-tools/scripts/` (same as step 7 of full setup).
```bash
# Recover a broken GPU
sudo nvidia-gpu-tools --recover-broken-gpu --gpu-bdf=<bdf>

# Secondary Bus Reset (all GPUs -- stop VM first)
chutes-reset-gpus

# Or target a single GPU
sudo nvidia-gpu-tools --reset-with-sbr --gpu-bdf=<bdf>

# Query current CC/PPCIe mode
sudo nvidia-gpu-tools --query-cc-mode
```

**Issue: "Network not accessible"**
```bash
# Check if public interface is correct
ip addr show

# Verify bridge and TAP are up
ip link show br0
ip link show vmtap0

# Ensure IP forwarding is enabled
sudo sysctl -w net.ipv4.ip_forward=1
```

---

## Access Points

Once the VM is running:

- **k3s API**: `https://<host_public_ip>:6443`
- **NodePort Services**: `<host_public_ip>:30000-32767`
- **SSH** (debug image only): `ssh -p 2222 root@<host_public_ip>`

**Note**: Production VMs do not have any remote access. All management is done via k3s API and attestation endpoints.

---

## File Locations

- **Base VM Image**: `/var/lib/chutes/base-images/tdx-guest.qcow2` (downloaded via `--download`)
- **Overlay Images**: `/var/lib/chutes/vm-overlays/tdx-<hostname>-<sha>.qcow2`
- **Firmware**: `firmware/TDVF.fd` (bundled with run-td)
- **Cache Volumes**: `host-tools/scripts/cache-<hostname>.raw`
- **Storage Volumes**: `host-tools/scripts/storage-<hostname>.raw`
- **Config Volumes**: `host-tools/scripts/config-<hostname>.qcow2`
- **VM Logs**: `/tmp/tdx-guest-td.log`
- **QEMU Logs**: `/tmp/qemu.log`
- **VM PID**: `/tmp/tdx-td-pid.pid`

---

## Security Considerations

- **Config Volume**: Contains sensitive credentials (miner seed/SS58, Docker Hub token). Store securely and restrict access.
- **Cache Volume**: LUKS-encrypted in production (unencrypted in debug). Used for HF/model caches.
- **Storage Volume**: LUKS-encrypted in production. Holds k3s state, containerd data, and kubelet pods.
- **Root Disk**: LUKS-encrypted. All OS and application data is protected.
- **Network Isolation**: VMs are isolated via NAT. Only exposed ports are accessible externally.
- **PPCIe Mode**: Provides memory encryption and attestation for GPUs, but not full CC mode protection.

---

## Additional Documentation

- [Cache Volume Details](docs/CACHE.md) - In-depth cache volume information
- [Configuration Guide](scripts/config/CONFIG-GUIDE.md) - Full config schema reference and validation
- [GPU Admin Tools](https://github.com/NVIDIA/gpu-admin-tools) - NVIDIA CC mode management (bundled as `nvidia-gpu-tools`)
- [Intel TDX Documentation](https://www.intel.com/content/www/us/en/developer/tools/trust-domain-extensions/overview.html)

---

## Development and Testing

### Create Test Configuration
```bash
# Create minimal test config
./quick-launch.sh --template
# Edit config.yaml with test values
./quick-launch.sh config.yaml --foreground
```

### Debug Mode
```bash
# Run in foreground to see all output
./quick-launch.sh config.yaml --foreground

# Enable QEMU debug logging (already enabled by default)
# Logs are written to /tmp/qemu.log

# Watch serial console in real-time
tail -f /tmp/tdx-guest-td.log
```
---

## Support and Contribution

For issues, questions, or contributions:
- Check existing documentation in `docs/`
- Review helper scripts in `scripts/`
- Examine the quick-launch orchestration logic
