# TEE VM Configuration Guide

## Overview

The TEE VM configuration system uses YAML files with JSON schema validation to ensure correct setup. This prevents common mistakes like missing required fields (e.g., cache or storage volume).

## Quick Start

### 1. Install Dependencies

```bash
pip3 install pyyaml jsonschema
```

### 2. Create Your Config

```bash
# Start from template
cp config/config.tmpl.yaml config.yaml

# Or use examples
cp config/config.prod.example.yaml config.yaml    # For production
cp config/config.debug.example.yaml config.yaml   # For debugging
```

### 3. Edit Config

Edit `config.yaml` with your settings. The schema will validate:
- Required fields are present
- Field types are correct
- IP addresses are valid
- Volume sizes use correct format (K/M/G/T)
- Network types are valid (tap/user)

### 4. Launch VM

```bash
./quick-launch.sh config.yaml
```

## Schema Validation

The parser automatically validates your config against `config-schema.json`. If validation fails, you'll see clear error messages:

```
Config validation error: 'containerd' is a required property
Path: volumes
```

### Validation Checks

- **Required fields**: hostname, miner credentials, network config, volumes
- **Format validation**: IP addresses, CIDR notation, volume sizes
- **Enum validation**: network.type must be "tap" or "user"
- **Pattern matching**: hostname must be valid DNS label
- **Type checking**: booleans, integers, strings

### Optional Validation

If `jsonschema` isn't installed, the parser will show a warning but continue. For production use, always install jsonschema:

```bash
pip3 install jsonschema
```

## Configuration Precedence

Values are resolved in this order (highest to lowest):

1. **CLI arguments** (`--hostname`, `--base-image`, `--overlay-dir`, `--docker-hub-username` / `--docker-hub-token` when **both** are set, etc.)
2. **YAML config file** (your config.yaml)
3. **Hard-coded defaults** (in quick-launch.sh)

For Docker Hub: if you pass **both** `--docker-hub-username` and `--docker-hub-token`, they override the optional `docker_hub` block in YAML. Otherwise `docker_hub.username` / `docker_hub.token` from YAML are used when present.

Example:
```bash
# Base image precedence:
./quick-launch.sh config.yaml --base-image /path/to/custom.qcow2
# Uses: /path/to/custom.qcow2 (CLI wins)

./quick-launch.sh config.yaml  # config.yaml has vm.base_image: "/var/lib/chutes/base-images/tdx-guest.qcow2"
# Uses: value from YAML

./quick-launch.sh config.yaml  # config.yaml has vm.base_image: ""
# Uses: default /var/lib/chutes/base-images/tdx-guest.qcow2
```

## Docker Hub (optional)

Optional `docker_hub` in YAML supplies credentials for authenticated Docker Hub pulls inside the guest (k3s/containerd and cosign). Without it, the VM uses anonymous Hub quota (often too low for busy boots).

```yaml
docker_hub:
  username: "your_dockerhub_username"
  token: "dckr_pat_..."   # prefer read-only Personal Access Token
```

- Schema: both `username` and `token` are required when `docker_hub` is present (`maxLength` 64 / 128).
- The host writes `docker-hub-username` and `docker-hub-token` onto the config volume (cleartext); treat the volume like other secrets.
- `quick-launch.sh` runs `volumes/create-config.sh` every launch: **new** qcow2 if the path is missing, otherwise **mount, remove everything at the volume root, then write** the current YAML-derived files. Stop the VM if QEMU still has that qcow2 open.
- See `config.tmpl.yaml`, `config.prod.example.yaml`, and `config.debug.example.yaml` for commented examples.

## Production vs Debug Configs

### Production Config (`config.prod.example.yaml`)

```yaml
vm:
  hostname: chutes-miner-prod-0
  base_image: "/var/lib/chutes/base-images/tdx-guest.qcow2"  # Encrypted image
  overlay_directory: ""  # Empty = /var/lib/chutes/vm-overlays/

volumes:
  cache:
    size: "5000G"
  storage:
    size: "500G"  # VM storage (containerd, kubelet-pods)
```

**Features:**
- Uses encrypted production image (built with `debug_build: false`)
- Larger volumes for production workloads
- SSH access removed (hardened)
- Commented optional `docker_hub` block for authenticated Hub pulls

### Debug Config (`config.debug.example.yaml`)

```yaml
vm:
  hostname: chutes-miner-debug-0
  base_image: "/var/lib/chutes/base-images/tdx-guest-debug.qcow2"  # Debug image
  overlay_directory: ""  # Empty = /var/lib/chutes/vm-overlays/

volumes:
  cache:
    size: "500G"  # Smaller
  storage:
    size: "100G"  # Smaller; see example file re: unencrypted debug storage
```

**Features:**
- Uses debug image (built with `debug_build: true`)
- Smaller volumes to save disk space
- SSH access preserved for debugging
- Commented optional `docker_hub` block (same semantics as prod)

**⚠️ CRITICAL: Never mix production and debug storage volumes** when encryption expectations differ — see comments in `config.debug.example.yaml`.

## Base Image and Overlay Configuration

### In Config File

```yaml
vm:
  base_image: "/var/lib/chutes/base-images/tdx-guest.qcow2"
  overlay_directory: ""  # Empty = /var/lib/chutes/vm-overlays/
```

Leave `base_image` empty to use default `/var/lib/chutes/base-images/tdx-guest.qcow2`.

### Via CLI Override

```bash
./quick-launch.sh config.yaml --base-image /path/to/tdx-guest.qcow2
./quick-launch.sh config.yaml --overlay-dir /custom/overlay/path
```

## Volume Auto-Generation

When volume paths are empty strings, they're auto-generated based on hostname:

```yaml
volumes:
  cache:
    path: ""  # Becomes: cache-<hostname>.raw (when auto-generated)
  storage:
    path: ""  # Becomes: storage-<hostname>.raw
  config:
    path: ""  # Becomes: config-<hostname>.qcow2
```

This ensures debug and production VMs use separate volumes.

## Common Validation Errors

### Missing Required Field

```
Config validation error: 'storage' is a required property
Path: volumes
```

**Fix:** Add the `storage` section to volumes (see `config.tmpl.yaml`).

### Invalid Volume Size Format

```
Config validation error: '500' does not match '^[0-9]+(K|M|G|T)$'
Path: volumes -> storage -> size
```

**Fix:** Add unit suffix:
```yaml
storage:
  size: "500G"  # Not "500"
```

### Invalid Network Type

```
Config validation error: 'bridge' is not one of ['tap', 'user']
Path: network -> type
```

**Fix:** Use valid network type:
```yaml
network:
  type: "tap"  # or "user"
```

### Invalid IP Address

```
Config validation error: '192.168.100' does not match format 'ipv4'
Path: network -> vm_ip
```

**Fix:** Use complete IP:
```yaml
network:
  vm_ip: "192.168.100.2"
```

## Migrating Old Configs

If you have configs from an older layout (e.g. missing `storage`), add the `volumes.storage` section to match `config-schema.json` and the current examples.

The schema validation will catch missing required sections, preventing runtime errors.

## Troubleshooting

### Schema Validation Skipped

```
Warning: jsonschema not installed. Skipping validation.
```

Install jsonschema for validation:
```bash
pip3 install jsonschema
```

### Parse Error

```
Error parsing YAML: mapping values are not allowed here
```

Check YAML syntax:
- Proper indentation (2 spaces)
- No tabs
- Colons have space after them: `key: value`
- Strings with special chars need quotes

### Unknown Properties

```
Config validation error: Additional properties are not allowed ('old_field' was unexpected)
```

Remove deprecated fields from your config. Check `config.tmpl.yaml` for current schema.

## Schema Reference

See `config-schema.json` for the complete schema definition. Key sections:

- **vm**: hostname (required), base_image (optional), overlay_directory (optional)
- **miner**: ss58, seed (both required)
- **network**: vm_ip, bridge_ip, dns, public_interface (all required), type, ssh_port (optional)
- **volumes**: cache, storage (both required), config (optional)
- **docker_hub** (optional): `username` and `token` — Docker Hub auth for guest pulls/cosign
- **devices**: bind_devices (optional, default: true)
- **runtime**: foreground (optional, default: false)

## Examples

### Minimal Valid Config

```yaml
vm:
  hostname: my-miner
  base_image: ""  # Optional: default /var/lib/chutes/base-images/tdx-guest.qcow2
  overlay_directory: ""  # Optional: default /var/lib/chutes/vm-overlays/

miner:
  ss58: "5Grw..."
  seed: "my-seed"

network:
  vm_ip: "192.168.100.2"
  bridge_ip: "192.168.100.1/24"
  dns: "8.8.8.8"
  public_interface: "ens9f0np0"

volumes:
  cache:
    size: "100G"
  storage:
    size: "50G"
```

### Full Config with All Options

See `config.tmpl.yaml` for a complete example with all available options and documentation.
