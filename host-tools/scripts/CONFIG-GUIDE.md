# TEE VM Configuration Guide

## Overview

The TEE VM configuration system uses YAML files with JSON schema validation to ensure correct setup. This prevents common mistakes like missing required fields (e.g., containerd cache volume).

## Quick Start

### 1. Install Dependencies

```bash
pip3 install pyyaml jsonschema
```

### 2. Create Your Config

```bash
# Start from template
cp config.tmpl.yaml config.yaml

# Or use examples
cp config.prod.example.yaml config.yaml    # For production
cp config.debug.example.yaml config.yaml   # For debugging
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

1. **CLI arguments** (`--hostname`, `--image`, etc.)
2. **YAML config file** (your config.yaml)
3. **Environment variables** (CHUTES_IMAGE for image path)
4. **Hard-coded defaults** (in quick-launch.sh)

Example:
```bash
# Image precedence:
CHUTES_IMAGE=old.qcow2 ./quick-launch.sh config.yaml --image new.qcow2
# Uses: new.qcow2 (CLI wins)

./quick-launch.sh config.yaml  # config.yaml has vm.image: "prod.qcow2"
# Uses: prod.qcow2 (YAML wins over env/defaults)

CHUTES_IMAGE=env.qcow2 ./quick-launch.sh config.yaml  # config.yaml has vm.image: ""
# Uses: env.qcow2 (env var wins over defaults)
```

## Production vs Debug Configs

### Production Config (`config.prod.example.yaml`)

```yaml
vm:
  hostname: chutes-miner-prod-0
  image: "tdx-guest.qcow2"  # Encrypted image

volumes:
  cache:
    size: "5000G"
  containerd:
    size: "500G"  # Encrypted containerd cache
```

**Features:**
- Uses encrypted production image (built with `debug_build: false`)
- Larger volumes for production workloads
- Encrypted containerd cache with validator key management
- SSH access removed (hardened)

### Debug Config (`config.debug.example.yaml`)

```yaml
vm:
  hostname: chutes-miner-debug-0
  image: "tdx-guest-debug.qcow2"  # Debug image

volumes:
  cache:
    size: "500G"  # Smaller
  containerd:
    size: "100G"  # UNENCRYPTED (different from prod!)
```

**Features:**
- Uses debug image (built with `debug_build: true`)
- Smaller volumes to save disk space
- Unencrypted containerd cache (no passphrase management)
- SSH access preserved for debugging

**⚠️ CRITICAL: Never mix production and debug containerd volumes!**

Debug VMs expect unencrypted containerd cache. If you attach a production encrypted volume, the init script will detect this and fail with a clear error.

## Image Path Configuration

### In Config File

```yaml
vm:
  image: "path/to/image.qcow2"
```

Leave empty (`image: ""`) to use environment variable or run-td default.

### Via Environment Variable

```bash
export CHUTES_IMAGE=/path/to/tdx-guest.qcow2
./quick-launch.sh config.yaml
```

### Via CLI Override

```bash
./quick-launch.sh config.yaml --image /path/to/image.qcow2
```

## Volume Auto-Generation

When volume paths are empty strings, they're auto-generated based on hostname:

```yaml
volumes:
  cache:
    path: ""  # Becomes: cache-<hostname>.qcow2
  containerd:
    path: ""  # Becomes: containerd-<hostname>.qcow2
  config:
    path: ""  # Becomes: config-<hostname>.qcow2
```

This ensures debug and production VMs use separate volumes.

## Common Validation Errors

### Missing Required Field

```
Config validation error: 'containerd' is a required property
Path: volumes
```

**Fix:** Add the containerd section to volumes:
```yaml
volumes:
  containerd:
    size: "500G"
    path: ""
```

### Invalid Volume Size Format

```
Config validation error: '500' does not match '^[0-9]+(K|M|G|T)$'
Path: volumes -> containerd -> size
```

**Fix:** Add unit suffix:
```yaml
containerd:
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

If you have configs from before containerd cache was added:

### Before
```yaml
volumes:
  cache:
    size: "5000G"
    path: ""
```

### After
```yaml
volumes:
  cache:
    size: "5000G"
    path: ""
  
  # ADD THIS - required for encrypted containerd storage
  containerd:
    size: "500G"
    path: ""
```

The schema validation will catch this immediately, preventing runtime errors.

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

- **vm**: hostname (required), image (optional)
- **miner**: ss58, seed (both required)
- **network**: vm_ip, bridge_ip, dns, public_interface (all required), type, ssh_port (optional)
- **volumes**: cache, containerd (both required), config (optional)
- **devices**: bind_devices (optional, default: true)
- **runtime**: foreground (optional, default: false)

## Examples

### Minimal Valid Config

```yaml
vm:
  hostname: my-miner

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
  containerd:
    size: "50G"
```

### Full Config with All Options

See `config.tmpl.yaml` for a complete example with all available options and documentation.
