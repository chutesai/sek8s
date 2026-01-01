# Debug Mode Configuration

## Overview

Debug mode allows building VM images without encryption and hardening, making development and debugging easier while still maintaining the same mount structure as production.

## Features

When `debug_build: true` is set:

1. **Skips LUKS encryption** - Root and containerd volumes remain unencrypted
2. **Skips access hardening** - SSH and remote access remain enabled
3. **Unencrypted containerd cache** - No passphrase management needed for debug VMs
4. **Maintains mount structure** - Containerd still mounts to `/var/lib/rancher/k3s/agent/containerd` for consistency

## Configuration

### Enable Debug Mode

Edit `ansible/k3s/inventory.yml` to override the default:

```yaml
all:
  vars:
    debug_build: true
```

Or set it per host/group:

```yaml
all:
  hosts:
    vm:
      debug_build: true
```

### Disable Debug Mode (Production)

```yaml
all:
  vars:
    debug_build: false
```

**Default:** `false` (production mode with encryption enabled)

The default is defined in each role's `defaults/main.yml` and can be overridden in inventory.

## Building Images

### Debug Image

```bash
# Set debug_build: true in inventory.yml, then build normally
make build-image

# The playbook will automatically:
# - Skip the luks role (no encryption)
# - Skip the harden-access role (keep SSH access)
# - Configure containerd cache for unencrypted device
```

### Production Image

```bash
# Set debug_build: false in inventory.yml (or use default), then build
make build-image

# The playbook will:
# - Run the luks role (encrypt root and setup boot scripts)
# - Run the harden-access role (remove SSH access)
# - Configure containerd cache for encrypted device with attestation
```

## Implementation Details

### Roles Affected

1. **luks role** - Skipped when `debug_build: true`
   - No root encryption
   - No boot scripts installed
   - No attestation setup

2. **harden-access role** - Skipped when `debug_build: true`
   - SSH access remains enabled
   - Remote access not removed

3. **cache-volume role** - Adapted for both modes
   - Production: Uses `/dev/mapper/containerd_cache` (encrypted)
   - Debug: Uses device by label `containerd-cache` (unencrypted)
   - Script detects mode via `DEBUG_MODE` environment variable

### Containerd Cache Behavior

#### Production Mode (`debug_build: false`)
- Boot script (`setup_containerd_cache`) unlocks containerd device using validator API
- Init service copies data from encrypted mapper device
- Mount unit mounts `/dev/mapper/containerd_cache`

#### Debug Mode (`debug_build: true`)
- No boot script needed (device not encrypted)
- Init service detects unencrypted device by label and copies data directly
- Mount unit still mounts to same path for consistency

### Service Configuration

The `containerd-cache-init.service` automatically receives the `DEBUG_MODE` environment variable from Ansible templating:

```ini
[Service]
Environment="DEBUG_MODE=true"   # or "false" based on ansible variable
ExecStart=/usr/local/bin/init-containerd-cache.sh
```

The init script (`init-containerd-cache.sh`) detects the mode:

```bash
if [ "$DEBUG_MODE" = "true" ]; then
    # Use unencrypted device directly
    DEVICE=$(blkid -l -o device -t LABEL="containerd-cache")
else
    # Use encrypted mapper device
    DEVICE="/dev/mapper/containerd_cache"
fi
```

## Creating Debug VMs

### With quick-launch.sh

```bash
# Create cache and containerd volumes (unencrypted for debug)
./host-tools/scripts/quick-launch.sh \
    --image debug-image.qcow2 \
    --config config.yaml \
    --cache-size 50G \
    --containerd-size 100G \
    --name debug-vm
```

### Manual Launch

```bash
# Create unencrypted volumes with labels
./host-tools/scripts/create-cache.sh cache.qcow2 50G tdx-cache
./host-tools/scripts/create-cache.sh containerd-cache.qcow2 100G containerd-cache

# Run VM with both volumes attached
./host-tools/scripts/run-td \
    --image debug-image.qcow2 \
    --config config.iso \
    --cache-volume cache.qcow2 \
    --containerd-volume containerd-cache.qcow2
```

## Benefits

### Development
- No passphrase management for debug VMs
- SSH access for debugging
- Faster boot (no attestation/encryption)
- Same containerd mount structure as production

### Testing
- Can test containerd cache behavior without encryption complexity
- Validate mount timing and data migration
- Debug systemd service dependencies

### Production Parity
- Containerd mounts to same location
- Same systemd service chain
- Same directory structure
- Only encryption differs

## Security Notes

⚠️ **Debug images should NEVER be used in production**

Debug images:
- Have no encryption (data at rest is readable)
- Have SSH access enabled (remote access possible)
- Skip security hardening
- Are intended for development/testing only

Always verify `debug_build: false` before building production images.

## Troubleshooting

### Debug image not skipping encryption
- Verify `debug_build: true` in `inventory.yml`
- Check Ansible output for "SKIPPED" on luks/harden-access tasks
- Ensure no manual tag overrides (`--tags luks` would force it)

### Containerd cache not mounting
- Check `journalctl -u containerd-cache-init.service` for errors
- Verify device label: `blkid | grep containerd-cache`
- Confirm DEBUG_MODE in service: `systemctl show containerd-cache-init.service | grep Environment`

### SSH access not available in debug image
- Verify harden-access role was skipped in build logs
- Check if `debug_build: true` was set before build
- Rebuild image with correct debug_build setting
