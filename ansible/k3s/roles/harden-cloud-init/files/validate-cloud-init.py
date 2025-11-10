#!/usr/bin/env python3
"""
validate-cloud-init.py - Secure cloud-init user-data validator for TEE TDX VMs
Only allows hostname setting and writing two specific files with strict validation
"""

import os
import sys
import yaml
import re
import shutil
from datetime import datetime
from pathlib import Path

# Configuration Constants
CLOUD_INIT_DATA_DIR = "/var/lib/cloud/seed/nocloud-net"
USER_DATA_FILE = os.path.join(CLOUD_INIT_DATA_DIR, "user-data")
BACKUP_DIR = "/var/lib/cloud/security-backups"
LOG_FILE = "/var/log/cloud-init-validator.log"

# Miner configuration paths
MINER_CREDS_DIR = "/var/lib/rancher/k3s/credentials"
MINER_SS58_PATH = os.path.join(MINER_CREDS_DIR, "miner-ss58")
MINER_SEED_PATH = os.path.join(MINER_CREDS_DIR, "miner-seed")

# Validation constants
ALLOWED_KEYS = ['hostname', 'write_files']
REQUIRED_FILE_PATHS = {MINER_SS58_PATH, MINER_SEED_PATH}

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

def validate_file_path(path):
    """Validate file path is one of the allowed paths"""
    allowed_paths = [MINER_SS58_PATH, MINER_SEED_PATH]
    return path in allowed_paths, f"Path must be one of: {allowed_paths}"

def validate_permissions(perms):
    """Validate file permissions are secure"""
    if perms != '0600':
        return False, "Permissions must be 0600"
    return True, "Permissions are valid"

def validate_owner(owner):
    """Validate file owner is root:root"""
    if owner != 'root:root':
        return False, "Owner must be root:root"
    return True, "Owner is valid"

def validate_file_content(path, content):
    """Validate file content based on file path"""
    if path == MINER_SS58_PATH:
        return validate_ss58_address(content)
    elif path == MINER_SEED_PATH:
        return validate_seed_content(content)
    else:
        return False, f"Unknown file path: {path}"

def create_backup(user_data_file, backup_dir):
    """Create backup of original user-data"""
    try:
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = int(datetime.now().timestamp())
        backup_path = os.path.join(backup_dir, f"user-data.backup.{timestamp}")
        shutil.copy2(user_data_file, backup_path)
        log(f"Backup created: {backup_path}")
        return True
    except Exception as e:
        log(f"Failed to create backup: {e}", "ERROR")
        return False

def move_invalid_file(user_data_file, backup_dir):
    """Move invalid user-data file to backup directory"""
    try:
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = int(datetime.now().timestamp())
        invalid_path = os.path.join(backup_dir, f"user-data.invalid.{timestamp}")
        shutil.move(user_data_file, invalid_path)
        log(f"Invalid user-data moved to: {invalid_path}")
        return True
    except Exception as e:
        log(f"Failed to move invalid file: {e}", "ERROR")
        return False

def validate_cloud_init_data():
    """Main validation function"""
    log("Starting cloud-init user-data validation")
    
    # Check if user-data file exists
    if not os.path.isfile(USER_DATA_FILE):
        log(f"No user-data file found at {USER_DATA_FILE}, allowing cloud-init to proceed")
        return True
    
    # Create backup of original user-data
    if not create_backup(USER_DATA_FILE, BACKUP_DIR):
        log("Failed to create backup, aborting validation", "ERROR")
        return False
    
    try:
        # Read user-data content
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check if it starts with #cloud-config
        if not content.startswith('#cloud-config'):
            log("user-data must start with #cloud-config", "ERROR")
            return False
        
        # Remove the #cloud-config header for YAML parsing
        yaml_content = '\n'.join(content.split('\n')[1:])
        
        # Parse YAML
        try:
            data = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            log(f"Invalid YAML format: {e}", "ERROR")
            return False
        
        if not isinstance(data, dict):
            log("YAML root must be a dictionary", "ERROR")
            return False
        
        # Whitelist approach - only allow specific keys
        for key in data.keys():
            if key not in ALLOWED_KEYS:
                log(f"Key '{key}' is not allowed. Only {ALLOWED_KEYS} are permitted", "ERROR")
                return False
        
        # Validate hostname if present
        if 'hostname' in data:
            is_valid, msg = validate_hostname(data['hostname'])
            if not is_valid:
                log(f"Invalid hostname: {msg}", "ERROR")
                return False
            log(f"Hostname validation passed: {data['hostname']}")
        
        # Validate write_files if present
        if 'write_files' in data:
            write_files = data['write_files']
            
            if not isinstance(write_files, list):
                log("write_files must be a list", "ERROR")
                return False
            
            if len(write_files) > 2:
                log("Maximum 2 files allowed in write_files", "ERROR")
                return False
            
            found_paths = set()
            
            for file_entry in write_files:
                if not isinstance(file_entry, dict):
                    log("Each write_files entry must be a dictionary", "ERROR")
                    return False
                
                # Validate required fields
                required_fields = ['path', 'content', 'permissions', 'owner']
                for field in required_fields:
                    if field not in file_entry:
                        log(f"Missing required field '{field}' in write_files entry", "ERROR")
                        return False
                
                # Validate no extra fields
                for field in file_entry.keys():
                    if field not in required_fields:
                        log(f"Unexpected field '{field}' in write_files entry", "ERROR")
                        return False
                
                # Validate path
                path = file_entry['path']
                is_valid, msg = validate_file_path(path)
                if not is_valid:
                    log(f"Invalid file path '{path}': {msg}", "ERROR")
                    return False
                
                if path in found_paths:
                    log(f"Duplicate file path '{path}'", "ERROR")
                    return False
                found_paths.add(path)
                
                # Validate content based on file type
                content_val = file_entry['content']
                is_valid, msg = validate_file_content(path, content_val)
                if not is_valid:
                    log(f"Invalid file content for '{path}': {msg}", "ERROR")
                    return False
                
                # Validate permissions
                perms = file_entry['permissions']
                is_valid, msg = validate_permissions(perms)
                if not is_valid:
                    log(f"Invalid permissions for '{path}': {msg}", "ERROR")
                    return False
                
                # Validate owner
                owner = file_entry['owner']
                is_valid, msg = validate_owner(owner)
                if not is_valid:
                    log(f"Invalid owner for '{path}': {msg}", "ERROR")
                    return False
            
            # Check that both required files are present if providing 2 files
            if len(write_files) == 2 and found_paths != REQUIRED_FILE_PATHS:
                log(f"When providing 2 files, both {REQUIRED_FILE_PATHS} must be present", "ERROR")
                return False
            
            log(f"write_files validation passed for {len(write_files)} file(s)")
        
        log("Cloud-init user-data validation passed successfully")
        return True
        
    except Exception as e:
        log(f"Validation failed with exception: {e}", "ERROR")
        return False

def main():
    """Main entry point"""
    try:
        # Ensure we're running as root for security operations
        if os.geteuid() != 0:
            log("This script must be run as root", "ERROR")
            sys.exit(1)
        
        # Validate cloud-init data
        if validate_cloud_init_data():
            log("Cloud-init user-data validation completed successfully")
            sys.exit(0)
        else:
            log("Cloud-init user-data validation failed", "ERROR")
            # Move the invalid user-data file to prevent cloud-init from using it
            if os.path.isfile(USER_DATA_FILE):
                if move_invalid_file(USER_DATA_FILE, BACKUP_DIR):
                    log("Invalid user-data moved to backup directory")
                else:
                    log("Failed to move invalid user-data file", "ERROR")
            sys.exit(1)
            
    except KeyboardInterrupt:
        log("Validation interrupted by user", "ERROR")
        sys.exit(1)
    except Exception as e:
        log(f"Unexpected error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()