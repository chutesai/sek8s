# TDX VM Host Setup Guide

This guide walks you through setting up a baremetal host to launch TDX-enabled VMs with GPU passthrough, isolated networking, and secure configuration using a streamlined, automated workflow.

## Prerequisites

- **Hardware**: Intel TDX-capable CPU, NVIDIA H100/H200 GPUs, NVSwitch (optional)
- **OS**: Ubuntu 25.04 (required for TDX host support)
- **Access**: Root/sudo privileges
- **Network**: Public network interface (e.g., `ens9f0np0`)
- **Python**: Python 3 with PyYAML (`pip3 install pyyaml`)

## Architecture Overview

The setup creates this architecture:
```
Internet ←→ Public Interface ←→ Bridge ←→ TAP ←→ TDX VM
                                            ↓
                                      GPU Passthrough (PPCIe Mode)
                                      Config Volume (credentials)
                                      Cache Volume (container storage)
                                      k3s Cluster
```

**Note**: GPUs run in PPCIe (Protected PCIe) mode to support multi-GPU passthrough in TDX environments. Full Confidential Computing mode does not support multiple GPU passthrough.

---

## Quick Start

For those familiar with the setup, here's the complete sequence:
```bash
# 1. Setup TDX host (one-time)
# Edit setup-tdx-config
nano tdx/setup-tdx-config
TDX_SETUP_ATTESTATION=1
cd tdx/setup-tdx-host && sudo ./setup-tdx-host.sh && sudo reboot

# 2. Configure PCCS
pccs-configure

# 3. Download guest image (see Step 3 below)

# 4. Create configuration from template
./quick-launch.sh --template
# Edit config.yaml with your settings

# 5. Launch VM (GPU binding + prep run automatically unless --skip-bind)
./quick-launch.sh config.yaml [--miner-ss58 <ss58>] [--miner-seed <seed>(no 0x prefix)]
```

---

## Detailed Setup

### Step 1: Install TDX Host Prerequisites

The TDX submodule provides host setup scripts that configure the kernel, QEMU, and firmware for TDX support.
```bash
# Clone the repository
git clone https://github.com/chutesai/sek8s.git
cd sek8s

# Initialize the TDX submodule
git submodule update --init --recursive

# Run the TDX host setup script
cd tdx
# Edit setup-tdx-config
nano setup-tdx-config
TDX_SETUP_ATTESTATION=1

sudo ./setup-tdx-host.sh

# Reboot to load TDX-enabled kernel
sudo reboot
```

**After reboot, verify TDX is available:**
```bash
dmesg | grep -i tdx
# Expected output should include: [    x.xxxxx] tdx: TDX module initialized
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

Download the prebuilt VM image from R2 to the name the scripts expect:
```bash
cd guest-tools/image
curl -O https://vm.chutes.ai/tdx-guest.qcow2
```

---

### Step 4: Create Configuration File

Navigate to the scripts directory and create your configuration from the template:
```bash
cd host-tools/scripts
./quick-launch.sh --template
```

This creates `config.yaml`. Edit it with your deployment settings:
```yaml
# VM Identity
vm:
  hostname: chutes-miner-tee-0

# Miner Credentials - Can also provide via CLI
miner:
  ss58: "<ss58>"  # Your actual SS58 address
  seed: "<seed>"  # Your actual miner seed, no 0x prefix

# Network Configuration
network:
  vm_ip: "192.168.100.2"
  bridge_ip: "192.168.100.1/24"
  dns: "8.8.8.8"
  public_interface: "ens9f0np0"  # Change to match your hardware

# Volume Configuration
volumes:
  cache:
    enabled: true
  size: "5000G"
    path: ""  # Leave empty to auto-create
  config:
    path: ""  # Leave empty to auto-create

# Device Configuration
devices:
  bind_devices: true  # Set to false to skip GPU binding

# Runtime Configuration
runtime:
  foreground: false  # Set to true for foreground mode (Ignored for prod image)
```

> **Note:** Memory, vCPU count, GPU MMIO, and PCI hole sizing are fixed inside
> `run-td` to preserve RTMR determinism. These canonical values are baked into
> the script and are not configurable via CLI flags.

**Required Configuration:**
- `hostname`: Unique identifier for this miner
- `network.public_interface`: Your host's public network interface name

**Optional Configuration:**
- `miner.ss58`: Your substrate SS58 address
- `miner.seed`: Your miner's seed phrase or private key, no 0x prefix

**Network Configuration:**
- The IP addresses should match your network topology
- Default gateway will be `bridge_ip` without the subnet mask
- Ensure `vm_ip` and `bridge_ip` are in the same subnet

---

### Step 5: Launch the VM

With your configuration file ready, launch the VM:
```bash
./quick-launch.sh config.yaml [--miner-ss58 <ss58>] [--miner-seed <seed>(no 0x prefix)]
```

The script will automatically:
1. **Validate host configuration** - Currently checks for `kvm_intel.tdx=1`
2. **Bind NVIDIA devices** - Runs `bind.sh` to attach GPUs/NVSwitch to `vfio-pci` (skip with `--skip-bind`)
3. **Prepare GPUs** - `run-td` invokes `tdx/gpu-cc/h100/setup-gpus.sh` to configure PPCIe/CC settings on each launch
4. **Create cache volume** - Set up container storage (if not existing)
5. **Create config volume** - Package credentials and network config
6. **Setup bridge networking** - Configure isolated network with NAT
7. **Launch TDX VM** - Start the VM with all components

**What happens during launch:**
- Cache volume is created at `cache-<hostname>.qcow2`
- Inside the guest, only the k3s containerd data is bind-mounted to `/var/snap/containerd`, so heavy OCI layers live on the cache volume while secrets remain on the encrypted root disk
- Config volume is created at `config-<hostname>.qcow2` (always fresh)
- Bridge network `br0` is configured with TAP interface
- NAT rules are applied for k3s API (6443) and NodePorts (30000-32767)
- VM starts in daemon mode with PID tracking

---

## Management Commands

### Check VM Status
```bash
# quick-launch does not expose --status; check the PID manually
cat /tmp/tdx-td-pid.pid && ps -p $(cat /tmp/tdx-td-pid.pid)

# If you launched with run-vm.sh instead:
cd host-tools/scripts
./run-vm.sh --status
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

GPU bindings are also reverted via `unbind.sh`; rerun `bind.sh` if you need to reattach without launching.

**Note**: Volume files (cache and config) are NOT deleted during cleanup.

---

## Advanced Usage

### Command Line Overrides

Override configuration file settings via command line:
```bash
# Run in foreground mode
./quick-launch.sh config.yaml --foreground

# Use existing cache volume
./quick-launch.sh config.yaml --cache-volume /path/to/existing-cache.qcow2

# Change cache size before creation
./quick-launch.sh config.yaml --cache-size 1T

# Override VM IP
./quick-launch.sh config.yaml --vm-ip 192.168.100.5
```

Note: Cache volume creation is required. `--skip-bind` only affects GPU binding to `vfio-pci` during launch.

### Manual Component Management

For advanced users who want to manage components separately:
```bash
# Manually bind devices
./bind.sh

# Manually create cache volume
sudo ./create-cache.sh cache.qcow2 5000G

# Manually create config volume
sudo ./create-config.sh config.qcow2 hostname ss58 seed vm-ip gateway dns

# Manually setup network
./setup-bridge.sh --bridge-ip 192.168.100.1/24 \
                  --vm-ip 192.168.100.2/24 \
                  --public-iface ens9f0np0

# Manually launch VM
./run-vm.sh --config-volume config.qcow2 \
            --cache-volume cache.qcow2 \
            --network-type tap \
            --net-iface vmtap0
```

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
./show-passthrough-devices.sh
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
./run-vm.sh --status

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
```bash
# Manual reset
./reset-gpus.sh
./bind.sh

OR

# Launch again, will auto rebind
./quick-launch.sh config.yaml
```

**Issue: "GPU appears stuck or unhealthy"**
```bash
# Use gpu-admin-tools recovery (no host driver needed and none should be installed)
git clone https://github.com/NVIDIA/gpu-admin-tools.git  # if not already present
cd gpu-admin-tools/host_tools/python
sudo python3 ./nvidia_gpu_tools.py --fix-broken-gpu --gpu-bdf=<bdf>
```
Drivers should not be installed on the host; rely on `nvidia_gpu_tools.py` utilities for recovery tasks.

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

- **VM Image (default target)**: `guest-tools/image/tdx-guest.qcow2` (or set `TD_IMG`)
- **Alternate image shipped**: `guest-tools/image/tdx-guest-ubuntu-24.04.qcow2` (symlink or export `TD_IMG` to use)
- **Firmware**: `/usr/share/ovmf/OVMF.fd` (run-td) or `firmware/TDVF.fd` (run-vm.sh)
- **Cache Volumes**: `host-tools/scripts/cache-*.qcow2`
- **Config Volumes**: `host-tools/scripts/config-*.qcow2`
- **VM Logs**: `/tmp/tdx-guest-td.log`
- **QEMU Logs**: `/tmp/qemu.log`
- **VM PID**: `/tmp/tdx-td-pid.pid`

---

## Security Considerations

- **Config Volume**: Contains sensitive credentials (miner seed/SS58). Store securely and restrict access.
- **Cache Volume**: Unencrypted storage for container images. Only use for non-sensitive data.
- **Root Disk**: Encrypted by TDX. All OS and application data is protected.
- **Network Isolation**: VMs are isolated via NAT. Only exposed ports are accessible externally.
- **PPCIe Mode**: Provides memory encryption and attestation for GPUs, but not full CC mode protection.

---

## Additional Documentation

- [Cache Volume Details](../docs/CACHE.md) - In-depth cache volume information
- [GPU Admin Tools](https://github.com/NVIDIA/gpu-admin-tools) - NVIDIA CC mode management
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
