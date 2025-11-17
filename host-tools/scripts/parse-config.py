#!/usr/bin/env python3
# parse-tee-config.py - Parse YAML config and output shell variables

import sys
import os
import yaml
import shlex

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
    
    # Extract values with defaults
    hostname = config.get('vm', {}).get('hostname', '')
    miner_ss58 = config.get('miner', {}).get('ss58', '')
    miner_seed = config.get('miner', {}).get('seed', '')
    
    network = config.get('network', {})
    vm_ip = network.get('vm_ip', '192.168.100.2')
    bridge_ip = network.get('bridge_ip', '192.168.100.1/24')
    vm_dns = network.get('dns', '8.8.8.8')
    public_iface = network.get('public_interface', 'ens9f0np0')
    
    volumes = config.get('volumes', {})
    cache_enabled = volumes.get('cache', {}).get('enabled', True)
    cache_size = volumes.get('cache', {}).get('size', '500G')
    cache_volume = volumes.get('cache', {}).get('path', '')
    config_volume = volumes.get('config', {}).get('path', '')
    
    devices = config.get('devices', {})
    bind_devices = devices.get('bind_devices', True)
    
    runtime = config.get('runtime', {})
    foreground = runtime.get('foreground', False)
    
    advanced = config.get('advanced', {})
    memory = advanced.get('memory', '1536G')
    vcpus = advanced.get('vcpus', 24)
    gpu_mmio_mb = advanced.get('gpu_mmio_mb', 262144)
    pci_hole_base_gb = advanced.get('pci_hole_base_gb', 2048)
    
    # Output shell variable assignments (properly escaped)
    print(f"HOSTNAME={shlex.quote(hostname)}")
    print(f"MINER_SS58={shlex.quote(miner_ss58)}")
    print(f"MINER_SEED={shlex.quote(miner_seed)}")
    print(f"VM_IP={shlex.quote(vm_ip)}")
    print(f"BRIDGE_IP={shlex.quote(bridge_ip)}")
    print(f"VM_DNS={shlex.quote(vm_dns)}")
    print(f"PUBLIC_IFACE={shlex.quote(public_iface)}")
    print(f"CACHE_SIZE={shlex.quote(cache_size)}")
    print(f"CACHE_VOLUME={shlex.quote(cache_volume)}")
    print(f"CONFIG_VOLUME={shlex.quote(config_volume)}")
    print(f"SKIP_BIND={'true' if not bind_devices else 'false'}")
    print(f"SKIP_CACHE={'true' if not cache_enabled else 'false'}")
    print(f"FOREGROUND={'true' if foreground else 'false'}")
    print(f"MEMORY={shlex.quote(memory)}")
    print(f"VCPUS={vcpus}")
    print(f"GPU_MMIO_MB={gpu_mmio_mb}")
    print(f"PCI_HOLE_BASE_GB={pci_hole_base_gb}")

if __name__ == '__main__':
    main()