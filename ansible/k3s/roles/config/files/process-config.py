#!/usr/bin/env python3
"""
validate-config.py - Secure config volume validator for TEE TDX VMs
Validates config files from mounted config volume and sets up system configuration
"""

import os
import sys
import re
import shutil
import yaml
from datetime import datetime
from pathlib import Path

# Configuration Constants
CONFIG_MOUNT_DIR = "/var/config"
BACKUP_DIR = "/var/lib/config-backups"
LOG_FILE = "/var/log/config-validator.log"

# Expected config files in the volume
EXPECTED_FILES = {
    "hostname": "/var/config/hostname",
    "miner-ss58": "/var/config/miner-ss58", 
    "miner-seed": "/var/config/miner-seed",
    "network-config.yaml": "/var/config/network-config.yaml"
}

# Target paths for configuration
HOSTNAME_TARGET = "/etc/hostname"
MINER_CREDS_DIR = "/var/lib/rancher/k3s/credentials"
MINER_SS58_TARGET = os.path.join(MINER_CREDS_DIR, "miner-ss58")
MINER_SEED_TARGET = os.path.join(MINER_CREDS_DIR, "miner-seed")
NETWORK_CONFIG_TARGET = "/etc/netplan/50-config-volume.yaml"

def log(message, level="INFO"):
    """Log a message with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {level}: {message}"
    print(log_entry)
    
    # Ensure log directory exists
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    
    # Write to log file
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry + "\n")

def validate_ss58_address(address):
    """Validate SS58 address format for Bittensor network"""
    if not isinstance(address, str):
        return False, "SS58 address must be a string"
    
    # Remove whitespace
    address = address.strip()
    
    # SS58 addresses are base58 encoded and typically 47-48 characters
    if len(address) < 40 or len(address) > 50:
        return False, f"SS58 address length invalid: {len(address)} (expected 40-50 chars)"
    
    # SS58 uses specific character set (base58)
    ss58_chars = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    if not all(c in ss58_chars for c in address):
        return False, "SS58 address contains invalid characters"
    
    # Bittensor addresses typically start with '5' for mainnet
    if not address.startswith('5'):
        return False, "SS58 address should start with '5' for Bittensor mainnet"
    
    return True, "SS58 address is valid"

def validate_seed_content(seed):
    """Validate seed content (hex string without 0x prefix)"""
    if not isinstance(seed, str):
        return False, "Seed must be a string"
    
    # Remove whitespace
    seed = seed.strip()
    
    # Check if it accidentally has 0x prefix (should be removed)
    if seed.startswith('0x') or seed.startswith('0X'):
        return False, "Seed should not have '0x' prefix"
    
    # Seed should be hex string, typically 64 characters (32 bytes)
    if len(seed) != 64:
        return False, f"Seed length invalid: {len(seed)} (expected 64 hex characters)"
    
    # Validate hex characters
    if not re.match(r'^[a-fA-F0-9]+$', seed):
        return False, "Seed contains invalid hex characters"
    
    return True, "Seed is valid"

def validate_hostname(hostname):
    """Validate hostname follows RFC standards and security requirements"""
    if not isinstance(hostname, str):
        return False, "Hostname must be a string"
    
    # Remove whitespace
    hostname = hostname.strip()
    
    if len(hostname) > 63:
        return False, "Hostname too long (max 63 characters)"
    
    if not re.match(r'^[a-zA-Z0-9-]+$', hostname):
        return False, "Hostname contains invalid characters"
    
    if hostname.startswith('-') or hostname.endswith('-'):
        return False, "Hostname cannot start or end with hyphen"
    
    # Only allow specific hostname pattern for this application
    if not re.match(r'^[a-zA-Z0-9]+-miner$', hostname):
        return False, "Hostname must follow pattern: [name]-miner"
    
    return True, "Hostname is valid"

def validate_network_config(network_config):
    """Validate network configuration YAML"""
    try:
        config = yaml.safe_load(network_config)
    except yaml.YAMLError as e:
        return False, f"Invalid YAML: {e}"
    
    if not isinstance(config, dict):
        return False, "Network config must be a dictionary"
    
    # Check for required version
    if config.get('version') != 2:
        return False, "Network config must have version: 2"
    
    # Validate ethernets section
    if 'ethernets' not in config:
        return False, "Network config must have 'ethernets' section"
    
    ethernets = config['ethernets']
    if not isinstance(ethernets, dict):
        return False, "ethernets must be a dictionary"
    
    # For now, just validate structure - don't enforce specific interface names
    for interface, settings in ethernets.items():
        if not isinstance(settings, dict):
            return False, f"Settings for interface {interface} must be a dictionary"
        
        # Allow either static config (addresses) or DHCP
        has_addresses = 'addresses' in settings
        has_dhcp = settings.get('dhcp4') is True or settings.get('dhcp6') is True
        
        if not has_addresses and not has_dhcp:
            return False, f"Interface {interface} must have either addresses or DHCP enabled"
    
    return True, "Network config is valid"

def create_backup_dir():
    """Create backup directory"""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        return True
    except Exception as e:
        log(f"Failed to create backup directory: {e}", "ERROR")
        return False

def read_config_file(filepath):
    """Read and return contents of config file"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        log(f"Failed to read {filepath}: {e}", "ERROR")
        return None

def write_target_file(content, target_path, mode=0o644, owner_uid=0, owner_gid=0):
    """Write content to target file with specified permissions"""
    try:
        # Ensure target directory exists
        target_dir = os.path.dirname(target_path)
        os.makedirs(target_dir, exist_ok=True)
        
        # Create backup if target exists
        if os.path.exists(target_path):
            timestamp = int(datetime.now().timestamp())
            backup_path = os.path.join(BACKUP_DIR, f"{os.path.basename(target_path)}.backup.{timestamp}")
            shutil.copy2(target_path, backup_path)
            log(f"Backup created: {backup_path}")
        
        # Write new content
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # Set permissions and ownership
        os.chmod(target_path, mode)
        os.chown(target_path, owner_uid, owner_gid)
        
        log(f"Successfully wrote {target_path}")
        return True
    except Exception as e:
        log(f"Failed to write {target_path}: {e}", "ERROR")
        return False

def validate_and_apply_config():
    """Main validation and configuration function"""
    log("Starting config volume validation")
    
    # Check if config mount directory exists and is mounted
    if not os.path.ismount(CONFIG_MOUNT_DIR):
        log(f"Config volume not mounted at {CONFIG_MOUNT_DIR}", "ERROR")
        return False
    
    # Create backup directory
    if not create_backup_dir():
        return False
    
    # Check all expected files exist
    missing_files = []
    for name, path in EXPECTED_FILES.items():
        if not os.path.isfile(path):
            missing_files.append(path)
    
    if missing_files:
        log(f"Missing required config files: {missing_files}", "ERROR")
        return False
    
    # Validate hostname
    hostname_content = read_config_file(EXPECTED_FILES["hostname"])
    if hostname_content is None:
        return False
    
    is_valid, msg = validate_hostname(hostname_content)
    if not is_valid:
        log(f"Invalid hostname: {msg}", "ERROR")
        return False
    log(f"Hostname validation passed: {hostname_content}")
    
    # Validate miner SS58
    ss58_content = read_config_file(EXPECTED_FILES["miner-ss58"])
    if ss58_content is None:
        return False
    
    is_valid, msg = validate_ss58_address(ss58_content)
    if not is_valid:
        log(f"Invalid miner SS58: {msg}", "ERROR")
        return False
    log("Miner SS58 validation passed")
    
    # Validate miner seed
    seed_content = read_config_file(EXPECTED_FILES["miner-seed"])
    if seed_content is None:
        return False
    
    is_valid, msg = validate_seed_content(seed_content)
    if not is_valid:
        log(f"Invalid miner seed: {msg}", "ERROR")
        return False
    log("Miner seed validation passed")
    
    # Validate network config
    network_content = read_config_file(EXPECTED_FILES["network-config.yaml"])
    if network_content is None:
        return False
    
    is_valid, msg = validate_network_config(network_content)
    if not is_valid:
        log(f"Invalid network config: {msg}", "ERROR")
        return False
    log("Network config validation passed")
    
    # All validations passed - apply configuration
    log("All validations passed, applying configuration...")
    
    # Apply hostname
    if not write_target_file(hostname_content + "\n", HOSTNAME_TARGET, 0o644):
        return False
    
    # Apply miner credentials (restrictive permissions)
    if not write_target_file(ss58_content + "\n", MINER_SS58_TARGET, 0o600):
        return False
    
    if not write_target_file(seed_content + "\n", MINER_SEED_TARGET, 0o600):
        return False
    
    # Apply network config
    if not write_target_file(network_content, NETWORK_CONFIG_TARGET, 0o644):
        return False
    
    # Set hostname immediately
    try:
        with open('/proc/sys/kernel/hostname', 'w') as f:
            f.write(hostname_content)
        log("Hostname applied immediately")
    except Exception as e:
        log(f"Warning: Could not set hostname immediately: {e}", "WARNING")
    
    log("Configuration applied successfully")
    return True

def main():
    """Main entry point"""
    try:
        # Ensure we're running as root for security operations
        if os.geteuid() != 0:
            log("This script must be run as root", "ERROR")
            sys.exit(1)
        
        # Validate and apply configuration
        if validate_and_apply_config():
            log("Config validation and application completed successfully")
            sys.exit(0)
        else:
            log("Config validation failed", "ERROR")
            sys.exit(1)
            
    except KeyboardInterrupt:
        log("Validation interrupted by user", "ERROR")
        sys.exit(1)
    except Exception as e:
        log(f"Unexpected error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()