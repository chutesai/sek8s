#!/usr/bin/env python3
# parse-tee-config.py - Parse YAML config and output shell variables

import sys
import os
import yaml
import shlex
import json

def validate_config(config, schema_path):
    """Validate config against JSON schema"""
    try:
        import jsonschema
    except ImportError:
        print("Warning: jsonschema not installed. Skipping validation.", file=sys.stderr)
        print("Install with: pip3 install jsonschema", file=sys.stderr)
        return True
    
    try:
        with open(schema_path, 'r') as f:
            schema = json.load(f)
        
        jsonschema.validate(instance=config, schema=schema)
        return True
    except jsonschema.ValidationError as e:
        print(f"Config validation error: {e.message}", file=sys.stderr)
        print(f"Path: {' -> '.join(str(p) for p in e.path)}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print(f"Warning: Schema file not found: {schema_path}", file=sys.stderr)
        print("Proceeding without validation.", file=sys.stderr)
        return True
    except Exception as e:
        print(f"Warning: Schema validation failed: {e}", file=sys.stderr)
        print("Proceeding without validation.", file=sys.stderr)
        return True

def main():
    if len(sys.argv) != 2:
        print("Usage: parse-config.py <config.yaml>", file=sys.stderr)
        sys.exit(1)
    
    config_file = sys.argv[1]
    
    if not os.path.exists(config_file):
        print(f"Error: Config file not found: {config_file}", file=sys.stderr)
        sys.exit(1)
    
    try:
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"Error parsing YAML: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading config file: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Validate against schema
    script_dir = os.path.dirname(os.path.abspath(__file__))
    schema_path = os.path.join(script_dir, 'config-schema.json')
    
    if not validate_config(config, schema_path):
        print("\nPlease fix the validation errors above.", file=sys.stderr)
        sys.exit(1)
    
    # Extract values with defaults
    vm_config = config.get('vm', {})
    hostname = vm_config.get('hostname', '')
    vm_image = vm_config.get('image', '')
    
    miner_ss58 = config.get('miner', {}).get('ss58', '')
    miner_seed = config.get('miner', {}).get('seed', '')
    
    network = config.get('network', {})
    vm_ip = network.get('vm_ip', '192.168.100.2')
    bridge_ip = network.get('bridge_ip', '192.168.100.1/24')
    vm_dns = network.get('dns', '8.8.8.8')
    public_iface = network.get('public_interface', 'ens9f0np0')
    network_type = network.get('type', 'tap')
    ssh_port = network.get('ssh_port', 2222)
    
    if 'advanced' in config:
        print("Error: 'advanced' section is no longer supported. Remove it to match the current schema.", file=sys.stderr)
        sys.exit(1)

    volumes = config.get('volumes', {})
    cache_cfg = volumes.get('cache', {})
    if 'enabled' in cache_cfg:
        print("Error: 'volumes.cache.enabled' has been removed. Delete it from your config.", file=sys.stderr)
        sys.exit(1)
    cache_size = cache_cfg.get('size', '5000G')
    cache_volume = cache_cfg.get('path', '')
    
    # Containerd cache configuration
    containerd_cfg = volumes.get('containerd', {})
    containerd_size = containerd_cfg.get('size', '500G')
    containerd_volume = containerd_cfg.get('path', '')
    
    config_volume = volumes.get('config', {}).get('path', '')
    
    devices = config.get('devices', {})
    bind_devices = devices.get('bind_devices', True)
    
    runtime = config.get('runtime', {})
    foreground = runtime.get('foreground', False)
    
    # Output shell variable assignments (properly escaped)
    print(f"HOSTNAME={shlex.quote(hostname)}")
    print(f"VM_IMAGE={shlex.quote(vm_image)}")
    print(f"MINER_SS58={shlex.quote(miner_ss58)}")
    print(f"MINER_SEED={shlex.quote(miner_seed)}")
    print(f"VM_IP={shlex.quote(vm_ip)}")
    print(f"BRIDGE_IP={shlex.quote(bridge_ip)}")
    print(f"VM_DNS={shlex.quote(vm_dns)}")
    print(f"PUBLIC_IFACE={shlex.quote(public_iface)}")
    print(f"NETWORK_TYPE={shlex.quote(network_type)}")
    print(f"SSH_PORT={shlex.quote(str(ssh_port))}")
    print(f"CACHE_SIZE={shlex.quote(cache_size)}")
    print(f"CACHE_VOLUME={shlex.quote(cache_volume)}")
    print(f"CONTAINERD_SIZE={shlex.quote(containerd_size)}")
    print(f"CONTAINERD_VOLUME={shlex.quote(containerd_volume)}")
    print(f"CONFIG_VOLUME={shlex.quote(config_volume)}")
    print(f"SKIP_BIND={'true' if not bind_devices else 'false'}")
    print(f"FOREGROUND={'true' if foreground else 'false'}")

if __name__ == '__main__':
    main()