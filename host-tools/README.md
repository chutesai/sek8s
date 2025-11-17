# TDX VM Host Setup Guide

This guide walks you through setting up a fresh baremetal host to launch a TDX-enabled VM with GPU passthrough, isolated networking, and secure boot configuration.

## Prerequisites

- **Hardware**: Intel TDX-capable CPU, NVIDIA H100/H200 GPUs, NVSwitch (optional)
- **OS**: Ubuntu 25.04 (required for TDX host support)
- **Access**: Root/sudo privileges
- **Network**: Public network interface (e.g., `ens9f0np0`)

## Architecture Overview

The setup creates this architecture:
```
Internet ←→ Public Interface ←→ Bridge ←→ TAP ←→ TDX VM
                                            ↓
                                      GPU Passthrough (PPCIe Mode)
                                      Cache Volume
                                      k3s Cluster
```

**Note**: GPUs run in PPCIe (Partial Protected Content Integration and Encryption) mode rather than full Confidential Computing mode, as multi-GPU passthrough is not supported in full CC mode.

---

## Step 1: Install TDX Host Prerequisites

The TDX submodule provides host setup scripts that configure the kernel, QEMU, and firmware for TDX support.
```bash
# Clone the repository
git clone <your-repo-url>
cd <your-repo>

# Initialize the TDX submodule
git submodule update --init --recursive

# Run the TDX host setup script
cd tdx/setup-tdx-host
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

## Step 2: Enable PPCIe Mode for NVIDIA GPUs

Configure GPUs to run in PPCIe mode to support multi-GPU passthrough in TDX environments.
```bash
# Set all NVIDIA GPUs to PPCIe mode
for gpu in /dev/nvidia[0-9]*; do
    sudo nvidia-smi -i ${gpu#/dev/nvidia} -cc PPCIe
done

# Verify PPCIe mode is enabled
nvidia-smi -q | grep "CC Mode"
# Expected: Current: PPCIe, Pending: PPCIe
```

**Note**: PPCIe mode persists across reboots but should be verified before each VM launch.

---

## Step 3: Bind NVIDIA GPUs to VFIO-PCI

Bind all NVIDIA GPUs and NVSwitches to the `vfio-pci` driver for passthrough to the VM.
```bash
cd host-tools/scripts

# Bind all NVIDIA devices (GPUs and NVSwitches)
sudo ./bind.sh

# Verify binding
ls -l /dev/vfio
lspci -k | grep -A3 NVIDIA
```

Expected: All NVIDIA devices should show `Kernel driver in use: vfio-pci`

---

## Step 4: Create Cache Volume

The cache volume provides unencrypted storage at `/var/snap` in the guest VM for container images and k3s data.
```bash
cd host-tools/scripts

# Create a 500GB cache volume
sudo ./create-cache.sh /path/to/cache-volume.qcow2 500G
```

**Important**: The label `tdx-cache` is automatically set and required. The TDX VM will verify this label at boot.

**Size recommendations:**
- Minimum: 100GB
- Recommended: 500GB - 1TB

For detailed information, see [CACHE.md](../docs/CACHE.md).

---

## Step 5: Configure Networking

The VM requires isolated networking with NAT and port forwarding. Currently only bridge-based networking is supported.
```bash
cd host-tools/scripts

# Create bridge network
sudo ./setup-bridge.sh \
  --bridge-ip 192.168.100.1/24 \
  --vm-ip 192.168.100.2/24 \
  --vm-dns 8.8.8.8 \
  --public-iface ens9f0np0
```

**Parameters:**
- `--bridge-ip`: Host bridge IP and subnet (default: `192.168.100.1/24`)
- `--vm-ip`: VM IP address (default: `192.168.100.2/24`)
- `--vm-dns`: DNS server for VM (default: `8.8.8.8`)
- `--public-iface`: Host's public network interface (change to match your hardware)

**The script configures:**
- Linux bridge (`br0`)
- TAP interface attached to bridge
- NAT and port forwarding:
  - k3s API: Port 6443 → VM:6443
  - k3s NodePorts: 30000-32767 → VM

**Save the TAP interface name** from the output (e.g., `vmtap-abc12345`) – you'll need it for the launch script.

---

## Step 6: Prepare Cloud-Init Configuration

Create cloud-init files to configure the VM at first boot.

### User-Data Configuration

Create `local/user-data.yaml`:
```yaml
#cloud-config
hostname: chutes-miner-tee-0

write_files:
  - path: /var/lib/rancher/k3s/credentials/miner-ss58
    content: "5EA***VW9"  # Your actual SS58 address
    permissions: '0600'
    owner: root:root
    
  - path: /var/lib/rancher/k3s/credentials/miner-seed
    content: "d61***f67"  # Your actual miner seed
    permissions: '0600'
    owner: root:root
```

**Replace the placeholder values:**
- `hostname`: Unique identifier for this miner
- `miner-ss58`: Your substrate SS58 address
- `miner-seed`: Your miner's seed phrase or private key

### Network Configuration

Create `local/network-config.yaml`:
```yaml
version: 2
ethernets:
  enp0s1:
    addresses:
      - 192.168.100.2/24  # Must match --vm-ip from bridge setup
    routes:
      - to: default
        via: 192.168.100.1  # Must match bridge gateway
    nameservers:
      addresses:
        - 8.8.8.8
        - 8.8.4.4
```

**Important**: The IP addresses must exactly match those used in Step 5.

---

## Step 7: Launch the TDX VM

Launch the VM with all components configured:
```bash
cd host-tools/scripts

sudo ./launch.sh \
  --image /path/to/tdx-guest-ubuntu-24.04-final.qcow2 \
  --cache-volume /path/to/cache-volume.qcow2 \
  --network-type tap \
  --net-iface vmtap-abc12345 \
  --cloud-init-user-data local/user-data.yaml \
  --cloud-init-network-config local/network-config.yaml \
  --vcpus 24 \
  --mem 1536G
```

**Required Parameters:**
- `--image`: Path to the TDX guest image
- `--cache-volume`: Path to cache volume from Step 4
- `--network-type`: Use `tap` for bridge networking
- `--net-iface`: TAP interface name from Step 5
- `--cloud-init-user-data`: Path to user-data.yaml
- `--cloud-init-network-config`: Path to network-config.yaml

**Optional Parameters:**
- `--vcpus`: Number of vCPUs (default: 24)
- `--mem`: Memory allocation (default: 1536G)
- `--gpu-mmio-mb`: MMIO per GPU in MB (default: 262144)
- `--nvswitch-mmio-mb`: MMIO per NVSwitch in MB (default: 32768)
- `--pci-hole-base-gb`: Base PCI hole size (default: 2048GB)

**Alternative**: If you prefer to specify network parameters directly instead of using a network-config file:
```bash
sudo ./launch.sh \
  --image /path/to/tdx-guest-ubuntu-24.04-final.qcow2 \
  --cache-volume /path/to/cache-volume.qcow2 \
  --network-type tap \
  --net-iface vmtap-abc12345 \
  --vm-ip 192.168.100.2 \
  --vm-gateway 192.168.100.1 \
  --vm-dns 8.8.8.8 \
  --cloud-init-user-data local/user-data.yaml \
  --vcpus 24 \
  --mem 1536G
```

The VM starts in daemon mode. Check status:
```bash
sudo ./launch.sh --status
```

---

## Step 8: Verify VM Operation

### Check VM Status
```bash
# View VM status
sudo ./launch.sh --status

# View serial console output
cat /tmp/tdx-guest-td.log
```

### Verify GPU Passthrough (from serial console or logs)
```bash
# Expected in logs: GPUs detected with PPCIe mode
grep -i nvidia /tmp/tdx-guest-td.log
```

### Verify k3s Cluster

The k3s API is accessible on the host's public IP at port 6443:
```bash
# Get kubeconfig from the VM logs or wait for attestation service to report
# k3s API: https://<host_public_ip>:6443
```

### Check Cache Volume Mount
```bash
# Expected in logs: /dev/vdb mounted at /var/snap
grep -i "cache\|vdb\|/var/snap" /tmp/tdx-guest-td.log
```

---

## Management Commands

### Check VM Status
```bash
sudo ./launch.sh --status
```

### View Serial Console
```bash
cat /tmp/tdx-guest-td.log
tail -f /tmp/tdx-guest-td.log  # Follow logs in real-time
```

### Stop VM
```bash
sudo ./launch.sh --clean
```

### Clean Up Network
```bash
sudo ./setup-bridge.sh --clean
```

### Re-enable PPCIe Mode After Reboot
```bash
# After host reboot, re-verify and set PPCIe mode
for gpu in /dev/nvidia[0-9]*; do
    sudo nvidia-smi -i ${gpu#/dev/nvidia} -cc PPCIe
done

# Re-bind GPUs
cd host-tools/scripts && sudo ./bind.sh
```

---

## Quick Reference

### Complete Setup Sequence
```bash
# 1. Setup TDX host
cd tdx/setup-tdx-host && sudo ./setup-tdx-host.sh && sudo reboot

# 2. Enable PPCIe mode for GPUs
for gpu in /dev/nvidia[0-9]*; do
    sudo nvidia-smi -i ${gpu#/dev/nvidia} -cc PPCIe
done

# 3. Bind GPUs
cd host-tools/scripts && sudo ./bind.sh

# 4. Create cache volume
sudo ./create-cache.sh /data/cache-volume.qcow2 500G

# 5. Setup network
sudo ./setup-bridge.sh \
  --bridge-ip 192.168.100.1/24 \
  --vm-ip 192.168.100.2/24 \
  --public-iface ens9f0np0

# 6. Create cloud-init files (see Step 6)

# 7. Launch VM
sudo ./launch.sh \
  --image /data/tdx-guest-ubuntu-24.04-final.qcow2 \
  --cache-volume /data/cache-volume.qcow2 \
  --network-type tap \
  --net-iface vmtap-<from-step-5> \
  --cloud-init-user-data local/user-data.yaml \
  --cloud-init-network-config local/network-config.yaml
```

### Access Points

- **k3s API**: `https://<host_public_ip>:6443`
- **NodePorts**: `<host_public_ip>:30000-32767`
- **Serial Console**: `/tmp/tdx-guest-td.log`

### Log Locations

- **QEMU log**: `/tmp/qemu.log`
- **Serial console**: `/tmp/tdx-guest-td.log`
- **VM PID**: `/tmp/tdx-td-pid.pid`

---

## Additional Documentation

- [Cache Volume Setup Details](../docs/CACHE.md)
- Guest VM Test Suites: `guest-tools/tests/`

---

## Important Notes

- **No Interactive Access**: Production VMs are configured without SSH or interactive console access. All management is done via k3s API and attestation endpoints.
- **PPCIe Mode**: Required for multi-GPU passthrough. Full Confidential Computing mode does not support multiple GPU passthrough.
- **Network Configuration**: Use either `--cloud-init-network-config` OR the individual `--vm-ip/--vm-gateway/--vm-dns` flags, not both.