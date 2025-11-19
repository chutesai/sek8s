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
cd tdx/setup-tdx-host && sudo ./setup-tdx-host.sh && sudo reboot

# 2. Clone NVIDIA GPU admin tools (one-time)
git clone https://github.com/NVIDIA/gpu-admin-tools

# 3. Enable PPCIe mode for all GPUs (after each host reboot)
cd gpu-admin-tools
for i in $(seq 0 $(($(lspci -nn | grep -c "10de") - 1))); do 
    sudo python3 ./nvidia_gpu_tools.py --set-ppcie-mode=on --reset-after-ppcie-mode-switch --gpu=$i
done

# 4. Create configuration from template
cd host-tools/scripts
./quick-launch.sh --template
# Edit config.yaml with your settings

# 5. Launch VM
./quick-launch.sh config.yaml
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
sudo ./setup-tdx-host.sh

# Reboot to load TDX-enabled kernel
sudo reboot
```

**After reboot, verify TDX is available:**
```bash
dmesg | grep -i tdx
# Expected output should include: [    x.xxxxx] tdx: TDX module initialized
```

The setup script also configures the following kernel parameters (verify in `/proc/cmdline`):
- `intel_iommu=on` - Enable Intel IOMMU
- `iommu=pt` - Use passthrough mode
- `kvm_intel.tdx=on` - Enable TDX support

---

### Step 2: Register the Platform

Ensure the platform is registered with Intel according to Intel's [docs](https://cc-enabling.trustedservices.intel.com/intel-tdx-enabling-guide/02/infrastructure_setup/#platform-registration)

### Step 3: Install NVIDIA GPU Admin Tools

Clone the NVIDIA GPU administration toolkit (one-time setup):
```bash
cd ~
git clone https://github.com/NVIDIA/gpu-admin-tools
cd gpu-admin-tools
```

This toolkit provides `nvidia_gpu_tools.py` for managing GPU confidential computing modes.

---

### Step 4: Enable PPCIe Mode for NVIDIA GPUs

Configure all GPUs and NVSwitches to run in PPCIe (Protected PCIe) mode. This step must be performed after each host reboot.
```bash
cd ~/gpu-admin-tools

# First, ensure CC mode is disabled on all devices
for i in $(seq 0 $(($(lspci -nn | grep -c "10de") - 1))); do 
    sudo python3 ./nvidia_gpu_tools.py --set-cc-mode=off --reset-after-cc-mode-switch --gpu=$i
done

# Then enable PPCIe mode on all devices (GPUs and NVSwitches)
for i in $(seq 0 $(($(lspci -nn | grep -c "10de") - 1))); do 
    sudo python3 ./nvidia_gpu_tools.py --set-ppcie-mode=on --reset-after-ppcie-mode-switch --gpu=$i
done

# Verify PPCIe mode is enabled
nvidia-smi -q | grep "CC Mode"
# Expected: Current: PPCIe, Pending: PPCIe
```

**Important Notes:**
- PPCIe mode persists across VM launches but NOT across host reboots
- You can safely ignore errors about NVSwitch devices not supporting CC mode
- Individual GPU selection: Replace `--gpu=$i` with `--gpu-bdf=xx:00.0` for specific devices

**Mode Reference:**
- `on` - Full Confidential Computing mode (single GPU only)
- `devtools` - Development mode with debugging enabled
- `off` - Normal operation (no protection)
- PPCIe mode - Protected PCIe for multi-GPU passthrough (our use case)

---

### Step 5: Create Configuration File

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

# Miner Credentials (required)
miner:
  ss58: "<ss58>"  # Your actual SS58 address
  seed: "<seed>"  # Your actual miner seed

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
    size: "500G"
    path: ""  # Leave empty to auto-create
  config:
    path: ""  # Leave empty to auto-create

# Device Configuration
devices:
  bind_devices: true  # Set to false to skip GPU binding

# Runtime Configuration
runtime:
  foreground: false  # Set to true for foreground mode

# Advanced Options (optional)
advanced:
  memory: "1536G"
  vcpus: 24
  gpu_mmio_mb: 262144
  pci_hole_base_gb: 2048
```

**Required Configuration:**
- `hostname`: Unique identifier for this miner
- `miner.ss58`: Your substrate SS58 address
- `miner.seed`: Your miner's seed phrase or private key
- `network.public_interface`: Your host's public network interface name

**Network Configuration:**
- The IP addresses should match your network topology
- Default gateway will be `bridge_ip` without the subnet mask
- Ensure `vm_ip` and `bridge_ip` are in the same subnet

---

### Step 6: Launch the VM

With your configuration file ready, launch the VM:
```bash
./quick-launch.sh config.yaml
```

The script will automatically:
1. **Validate host configuration** - Check for required kernel parameters
2. **Reset and bind GPUs** - Prepare GPUs for passthrough using VFIO-PCI
3. **Create cache volume** - Set up container storage (if not existing)
4. **Create config volume** - Package credentials and network config
5. **Setup bridge networking** - Configure isolated network with NAT
6. **Launch TDX VM** - Start the VM with all components

**What happens during launch:**
- Cache volume is created at `cache-<hostname>.qcow2` (if needed)
- Config volume is created at `config-<hostname>.qcow2` (always fresh)
- Bridge network `br0` is configured with TAP interface
- NAT rules are applied for k3s API (6443) and NodePorts (30000-32767)
- VM starts in daemon mode with PID tracking

---

## Management Commands

### Check VM Status
```bash
./quick-launch.sh config.yaml --status
# Or directly:
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
- VFIO-PCI device bindings

**Note**: Volume files (cache and config) are NOT deleted during cleanup.

---

## Advanced Usage

### Command Line Overrides

Override configuration file settings via command line:
```bash
# Run in foreground mode
./quick-launch.sh config.yaml --foreground

# Skip device binding (GPUs already bound)
./quick-launch.sh config.yaml --skip-bind

# Use existing cache volume
./quick-launch.sh config.yaml --cache-volume /path/to/existing-cache.qcow2

# Skip cache volume entirely
./quick-launch.sh config.yaml --skip-cache

# Override VM IP
./quick-launch.sh config.yaml --vm-ip 192.168.100.5
```

### Manual Component Management

For advanced users who want to manage components separately:
```bash
# Manually bind devices
./bind.sh

# Manually create cache volume
sudo ./create-cache.sh cache.qcow2 500G

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
cat /proc/cmdline | grep -E 'intel_iommu|iommu=pt|kvm_intel.tdx'

# Verify TDX module
dmesg | grep -i tdx

# Check IOMMU groups
ls -l /sys/kernel/iommu_groups/
```

### Verify GPU Configuration
```bash
# Check PPCIe mode status
nvidia-smi -q | grep "CC Mode"

# List NVIDIA devices
lspci -nn -d 10de:

# Check VFIO bindings
./show-passthrough-devices.sh

# Verify device reset capability
ls -l /sys/bus/pci/drivers/vfio-pci/*/reset
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

**Issue: "intel_iommu=on not found"**
```bash
# Add to /etc/default/grub:
GRUB_CMDLINE_LINUX="intel_iommu=on iommu=pt kvm_intel.tdx=on"
sudo update-grub && sudo reboot
```

**Issue: "GPUs not in PPCIe mode after reboot"**
```bash
# PPCIe mode must be re-enabled after each host reboot
cd ~/gpu-admin-tools
for i in $(seq 0 $(($(lspci -nn | grep -c "10de") - 1))); do 
    sudo python3 ./nvidia_gpu_tools.py --set-ppcie-mode=on --reset-after-ppcie-mode-switch --gpu=$i
done
```


**Issue: "VM fails to start with GPU errors"**
```bash
# Manual reset
./reset-gpus.sh
./bind.sh

OR

# Launch again, will auto rebind
./quick-launch.sh config.yaml
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
- **SSH** (if enabled): `ssh -p 2222 root@<host_public_ip>`

**Note**: Production VMs are typically configured without SSH access. All management is done via k3s API and attestation endpoints.

---

## File Locations

- **VM Images**: `guest-tools/image/tdx-guest-ubuntu-24.04-final.qcow2`
- **TDVF Firmware**: `firmware/TDVF.fd`
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
./quick-launch.sh config.yaml --foreground --skip-cache
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

### Performance Tuning

Adjust advanced options in `config.yaml`:
```yaml
advanced:
  memory: "1536G"              # Adjust based on workload
  vcpus: 24                    # Match physical core count
  gpu_mmio_mb: 262144          # Per-GPU MMIO (256GB default)
  pci_hole_base_gb: 2048       # Minimum PCI hole size
```

---

## Support and Contribution

For issues, questions, or contributions:
- Check existing documentation in `docs/`
- Review helper scripts in `scripts/`
- Examine the quick-launch orchestration logic
