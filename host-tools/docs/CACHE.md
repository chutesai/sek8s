# Cache Volume Setup for TDX VMs

This guide explains how to create and prepare an unencrypted cache volume for use with TDX VMs. The cache volume will be mounted at `/var/snap` in the guest VM to provide additional storage separate from the encrypted root volume.

## Prerequisites

- `qemu-img` (part of QEMU, usually already installed)
- `qemu-nbd` (Network Block Device utility for QEMU)
- `mkfs.ext4` (standard Linux filesystem utilities)
- Root/sudo access on the host
- NBD kernel module (standard on most Linux distributions)

## Quick Start

```bash
# Create a 5TB cache volume
./scripts/create-cache-volume.sh cache-volume.qcow2 5000G
```

## Manual Setup (Step-by-Step)

If you prefer to create the volume manually or the script is not available, follow these steps:

### 1. Create the qcow2 Image

Create an empty qcow2 image file. Adjust the size as needed for your use case:

```bash
qemu-img create -f qcow2 cache-volume.qcow2 5000G
```

**Size recommendations:**
- Minimum: 100G
- Recommended: 5T (5000G) or larger for production miners
- Maximum: Limited by host disk space

### 2. Load the NBD Kernel Module

The NBD (Network Block Device) module allows us to expose the qcow2 file as a block device:

```bash
sudo modprobe nbd max_part=8
```

**Note:** This only needs to be done once per boot. To make it persistent across reboots, add `nbd` to `/etc/modules`:

```bash
echo "nbd" | sudo tee -a /etc/modules
```

### 3. Connect the qcow2 to an NBD Device

Expose the qcow2 file as a block device:

```bash
sudo qemu-nbd --connect=/dev/nbd0 cache-volume.qcow2
```

**Troubleshooting:**
- If `/dev/nbd0` is busy, try `/dev/nbd1`, `/dev/nbd2`, etc.
- Verify connection: `ls -l /dev/nbd0` should show a block device

### 4. Format with ext4 and Label

Format the device with an ext4 filesystem and the required label:

```bash
sudo mkfs.ext4 -L tdx-cache-storage /dev/nbd0
```

**Important:** The label `tdx-cache-storage` is required. The TDX VM will verify this label at boot time and refuse to start if it's incorrect.

### 5. Disconnect the NBD Device

Clean up by disconnecting the NBD device:

```bash
sudo qemu-nbd --disconnect /dev/nbd0
```

### 6. Verify the Volume (Optional but Recommended)

Reconnect and verify the filesystem and label:

```bash
# Reconnect
sudo qemu-nbd --connect=/dev/nbd0 cache-volume.qcow2

# Verify filesystem and label
sudo blkid /dev/nbd0

# Expected output should include:
# TYPE="ext4" LABEL="tdx-cache-storage"

# Disconnect
sudo qemu-nbd --disconnect /dev/nbd0
```

## Complete Example

Here's a complete example creating a 5TB cache volume:

```bash
# Create the volume
qemu-img create -f qcow2 /path/to/cache-volume.qcow2 5000G

# Load NBD module (if not already loaded)
sudo modprobe nbd max_part=8

# Connect to NBD
sudo qemu-nbd --connect=/dev/nbd0 /path/to/cache-volume.qcow2

# Format with label
sudo mkfs.ext4 -L tdx-cache-storage /dev/nbd0

# Verify (optional)
sudo blkid /dev/nbd0

# Disconnect
sudo qemu-nbd --disconnect /dev/nbd0

echo "Cache volume ready: /path/to/cache-volume.qcow2"
```

## Using the Cache Volume with run-tdx.sh

Once the cache volume is prepared, pass it to the TDX launch script:

```bash
./run-tdx.sh \
  --cache-volume /path/to/cache-volume.qcow2 \
  --hostname my-tdx-miner \
  --miner-ss58 'your_ss58_address' \
  --miner-seed 'your_seed' \
  --network-type macvtap \
  --net-iface vmnet-12345678 \
  --vm-ip 192.168.100.2 \
  --vm-gateway 192.168.100.1
```

## Verification at Boot

The TDX VM will automatically verify the cache volume during boot:

1. **Device check**: Confirms `/dev/vdb` exists
2. **Filesystem check**: Confirms the filesystem is ext4
3. **Label check**: Confirms the label is exactly `tdx-cache-storage`
4. **Mount check**: Mounts to `/var/snap`

**If any check fails, the VM will immediately shut down.** Check the serial log for error details:

```bash
./run-tdx.sh --status
# Or directly:
cat /tmp/tdx-guest-td.log
```

## Troubleshooting

### "Device or resource busy" when connecting NBD

Another process is using the NBD device. Try:
```bash
# List NBD devices in use
sudo qemu-nbd --list

# Try a different NBD device
sudo qemu-nbd --connect=/dev/nbd1 cache-volume.qcow2
```

### "No such device" - NBD module not loaded

Load the kernel module:
```bash
sudo modprobe nbd max_part=8
```

### mkfs.ext4 command not found

Install filesystem utilities:
```bash
# Ubuntu/Debian
sudo apt-get install e2fsprogs

# RHEL/CentOS
sudo yum install e2fsprogs
```

### Changing the volume size later

You can resize a qcow2 volume, but it requires additional steps:

```bash
# Increase qcow2 size
qemu-img resize cache-volume.qcow2 +200G

# Connect and resize filesystem
sudo qemu-nbd --connect=/dev/nbd0 cache-volume.qcow2
sudo resize2fs /dev/nbd0
sudo qemu-nbd --disconnect /dev/nbd0
```

### Verifying label on an existing volume

If you're unsure if a volume has the correct label:

```bash
sudo qemu-nbd --connect=/dev/nbd0 cache-volume.qcow2
sudo blkid /dev/nbd0 | grep LABEL
sudo qemu-nbd --disconnect /dev/nbd0
```

To change a label on an existing formatted volume:

```bash
sudo qemu-nbd --connect=/dev/nbd0 cache-volume.qcow2
sudo e2label /dev/nbd0 tdx-cache-storage
sudo qemu-nbd --disconnect /dev/nbd0
```

## Security Considerations

- **Unencrypted**: The cache volume is NOT encrypted. Only store non-sensitive data.
- **Host access**: The host machine can read the cache volume contents.
- **Integrity**: Consider storing checksums of critical data to detect tampering.
- **Isolation**: Keep cache volumes separate per VM to prevent cross-contamination.

## Storage Best Practices

- **Location**: Store cache volumes on fast storage (SSD/NVMe) for best performance
- **Backups**: The cache is meant for temporary/reproducible data, but back up if needed
- **Thin provisioning**: qcow2 uses thin provisioning - the file grows as data is written
- **Monitoring**: Check actual disk usage with `qemu-img info cache-volume.qcow2`

## File Locations

- Cache volumes: Store alongside your VM images (e.g., `guest-tools/volumes/`)
- Documentation: This file should be in your repository root or docs folder
- Scripts: Helper scripts in `scripts/` or project root

## Next Steps

After creating the cache volume:

1. Update your TDX base image with the verification service (see CACHE-VOLUME-GUEST-SETUP.md)
2. Test the cache volume with a test VM before production use
3. Document your specific cache volume locations and sizes for your deployment

## References

- [QEMU NBD documentation](https://qemu.readthedocs.io/en/latest/tools/qemu-nbd.html)
- [qemu-img documentation](https://qemu.readthedocs.io/en/latest/tools/qemu-img.html)
- [ext4 filesystem documentation](https://www.kernel.org/doc/html/latest/filesystems/ext4/index.html)