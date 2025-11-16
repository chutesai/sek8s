#!/bin/bash
# quick-launch-tee.sh - TEE VM orchestration with clean YAML parsing
# Uses Python for YAML parsing, shell for orchestration

set -e

# Default values
CONFIG_FILE=""
HOSTNAME=""
MINER_SS58=""
MINER_SEED=""
VM_IP="192.168.100.2"
BRIDGE_IP="192.168.100.1/24"
VM_DNS="8.8.8.8"
PUBLIC_IFACE="ens9f0np0"
FOREGROUND=false
CACHE_SIZE="500G"
CACHE_VOLUME=""
CONFIG_VOLUME=""
SKIP_BIND=false
SKIP_CACHE=false
MEMORY="1536G"
VCPUS=24
GPU_MMIO_MB=262144
PCI_HOLE_BASE_GB=2048

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    *.yaml|*.yml)
      CONFIG_FILE="$1"
      shift
      ;;
    --config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    --hostname) HOSTNAME="$2"; shift 2 ;;
    --miner-ss58) MINER_SS58="$2"; shift 2 ;;
    --miner-seed) MINER_SEED="$2"; shift 2 ;;
    --vm-ip) VM_IP="$2"; shift 2 ;;
    --bridge-ip) BRIDGE_IP="$2"; shift 2 ;;
    --vm-dns) VM_DNS="$2"; shift 2 ;;
    --public-iface) PUBLIC_IFACE="$2"; shift 2 ;;
    --cache-size) CACHE_SIZE="$2"; shift 2 ;;
    --cache-volume) CACHE_VOLUME="$2"; shift 2 ;;
    --config-volume) CONFIG_VOLUME="$2"; shift 2 ;;
    --skip-bind) SKIP_BIND=true; shift ;;
    --skip-cache) SKIP_CACHE=true; shift ;;
    --foreground) FOREGROUND=true; shift ;;
    --clean)
      echo "=== Cleaning Up TEE VM Environment ==="
      ./run-vm.sh --clean 2>/dev/null || true
      ./setup-bridge.sh --clean 2>/dev/null || true
      if [ -f "./bind.sh" ]; then
        ./bind.sh --unbind 2>/dev/null || true
      fi
      echo "Cleanup complete."
      exit 0
      ;;
    --template)
      if [ -f "config.tmpl.yaml" ]; then
        cp config.tmpl.yaml "config.yaml"
        echo "Created: config.yaml"
        echo "Edit this file with your configuration, then run:"
        echo "  $0 config.yaml"
      else
        echo "Error: Template file config.tmpl.yaml not found"
      fi
      exit 0
      ;;
    --help)
      cat << EOF
Usage: $0 [config.yaml] [options]

TEE VM orchestration with YAML configuration support.

Config File:
  config.yaml               Use YAML configuration file
  --config FILE             Specify config file explicitly
  --template                Create template config file from template

Command Line Options (override config):
  --hostname NAME           VM hostname
  --miner-ss58 VALUE        Miner SS58 credential
  --miner-seed VALUE        Miner seed credential
  --vm-ip IP                VM IP address
  --cache-volume PATH       Use existing cache volume
  --skip-bind               Skip device binding
  --skip-cache              Skip cache volume
  --foreground              Run in foreground
  --clean                   Clean up everything

Examples:
  # Create template config
  $0 --template
  
  # Use config file
  $0 config.yaml
  
  # Use config with overrides
  $0 config.yaml --foreground --skip-bind
  
  # Command line only
  $0 --hostname miner --miner-ss58 'ss58' --miner-seed 'seed'

Requirements:
  - Python 3 with PyYAML (pip3 install pyyaml)
  - All helper scripts (bind.sh, create-*.sh, setup-bridge.sh, run-vm.sh)
EOF
      exit 0
      ;;
    *) echo "Unknown option: $1. Use --help for usage."; exit 1 ;;
  esac
done

# Load config file if provided
if [[ -n "$CONFIG_FILE" ]]; then
  echo "Loading configuration from: $CONFIG_FILE"
  
  # Check if Python and PyYAML are available
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python 3 not found. Install with: sudo apt install python3"
    exit 1
  fi
  
  if ! python3 -c "import yaml" 2>/dev/null; then
    echo "Error: PyYAML not found. Install with: pip3 install pyyaml"
    exit 1
  fi
  
  # Check if our config parser exists
  if [[ ! -f "./parse-config.py" ]]; then
    echo "Error: parse-config.py not found in current directory"
    exit 1
  fi
  
  # Parse config and load variables
  CONFIG_OUTPUT=$(python3 ./parse-config.py "$CONFIG_FILE" 2>&1)
  CONFIG_EXIT_CODE=$?
  
  if [[ $CONFIG_EXIT_CODE -ne 0 ]]; then
    echo "Error parsing config file:"
    echo "$CONFIG_OUTPUT"
    exit 1
  fi
  
  # Load the parsed variables
  eval "$CONFIG_OUTPUT"
  echo "✓ Configuration loaded successfully"
fi

# Validate required parameters
if [[ -z "$HOSTNAME" || -z "$MINER_SS58" || -z "$MINER_SEED" ]]; then
  echo "Error: Missing required configuration:"
  [[ -z "$HOSTNAME" ]] && echo "  - hostname"
  [[ -z "$MINER_SS58" ]] && echo "  - miner.ss58"
  [[ -z "$MINER_SEED" ]] && echo "  - miner.seed"
  echo ""
  echo "Provide via config file or command line:"
  echo "  $0 --template  # Create template"
  echo "  $0 config.yaml"
  exit 1
fi

echo ""
echo "=== TEE VM Orchestration ==="
echo "Config: ${CONFIG_FILE:-command line}"
echo "Hostname: $HOSTNAME"
echo "VM IP: $VM_IP"
echo "Bridge IP: $BRIDGE_IP"
echo "Cache: ${SKIP_CACHE:+Skipped}${SKIP_CACHE:-$CACHE_SIZE}"
echo "Binding: ${SKIP_BIND:+Skipped}${SKIP_BIND:-Enabled}"
echo ""

# Step 0: Verify host configuration
echo "Step 0: Verifying host configuration..."
HOST_CMDLINE=$(cat /proc/cmdline 2>/dev/null || echo "")

# Check for intel_iommu=on
if ! echo "$HOST_CMDLINE" | grep -q "intel_iommu=on"; then
  echo "✗ Error: Host kernel missing 'intel_iommu=on' parameter"
  echo "  Add to /etc/default/grub: GRUB_CMDLINE_LINUX=\"... intel_iommu=on ...\""
  echo "  Then run: sudo update-grub && sudo reboot"
  exit 1
fi

# Check for iommu=pt
if ! echo "$HOST_CMDLINE" | grep -q "iommu=pt"; then
  echo "✗ Error: Host kernel missing 'iommu=pt' parameter"
  echo "  Add to /etc/default/grub: GRUB_CMDLINE_LINUX=\"... iommu=pt ...\""
  echo "  Then run: sudo update-grub && sudo reboot"
  exit 1
fi

# Check for kvm_intel.tdx=on
if ! echo "$HOST_CMDLINE" | grep -q "kvm_intel.tdx=on"; then
  echo "✗ Error: Host kernel missing 'kvm_intel.tdx=on' parameter"
  echo "  Add to /etc/default/grub: GRUB_CMDLINE_LINUX=\"... kvm_intel.tdx=on ...\""
  echo "  Then run: sudo update-grub && sudo reboot"
  exit 1
fi

echo "✓ Host IOMMU configuration verified"
echo "✓ Host TDX enabled"
echo ""

# Step 1: Bind devices
if [[ "$SKIP_BIND" != "true" ]]; then
  echo "Step 1: Binding GPU and NVSwitch devices..."
  if [ -f "./bind.sh" ]; then
    ./bind.sh --bind && echo "✓ Devices bound" || echo "⚠ Device binding failed"
  else
    echo "⚠ bind.sh not found, skipping"
  fi
else
  echo "Step 1: Skipping device binding"
fi
echo ""

# Step 2: Cache volume
if [[ "$SKIP_CACHE" != "true" ]]; then
  echo "Step 2: Setting up cache volume..."
  if [[ -n "$CACHE_VOLUME" ]] && [[ -f "$CACHE_VOLUME" ]]; then
    echo "✓ Using existing cache volume: $CACHE_VOLUME"
  else
    CACHE_VOLUME="cache-${HOSTNAME}.qcow2"
    if [[ -f "$CACHE_VOLUME" ]]; then
      echo "✓ Using existing cache volume: $CACHE_VOLUME"
    else
      echo "Creating cache volume: $CACHE_VOLUME ($CACHE_SIZE)"
      sudo ./create-cache.sh "$CACHE_VOLUME" "$CACHE_SIZE" && echo "✓ Cache volume created"
    fi
  fi
else
  echo "Step 2: Skipping cache volume"
  CACHE_VOLUME=""
fi
echo ""

# Step 3: Config volume
echo "Step 3: Setting up config volume..."
if [[ -n "$CONFIG_VOLUME" ]] && [[ -f "$CONFIG_VOLUME" ]]; then
  echo "✓ Using existing config volume: $CONFIG_VOLUME"
else
  CONFIG_VOLUME="config-${HOSTNAME}.qcow2"
  [[ -f "$CONFIG_VOLUME" ]] && sudo rm -f "$CONFIG_VOLUME"
  
  echo "Creating config volume: $CONFIG_VOLUME"
  sudo ./create-config.sh "$CONFIG_VOLUME" "$HOSTNAME" "$MINER_SS58" "$MINER_SEED" "$VM_IP" "${BRIDGE_IP%/*}" "$VM_DNS"
  echo "✓ Config volume created"
fi
echo ""

# Step 4: Bridge networking
echo "Step 4: Setting up bridge networking..."
BRIDGE_OUTPUT=$(./setup-bridge.sh \
  --bridge-ip "$BRIDGE_IP" \
  --vm-ip "${VM_IP}/24" \
  --vm-dns "$VM_DNS" \
  --public-iface "$PUBLIC_IFACE" 2>&1)

TAP_IFACE=$(echo "$BRIDGE_OUTPUT" | grep "Network interface:" | awk '{print $3}')
if [[ -z "$TAP_IFACE" ]]; then
  echo "Error: Failed to extract TAP interface"
  exit 1
fi
echo "✓ Bridge configured (TAP: $TAP_IFACE)"
echo ""

# Step 5: Launch VM
echo "Step 5: Launching TEE VM..."
LAUNCH_ARGS=(
  --config-volume "$CONFIG_VOLUME"
  --net-iface "$TAP_IFACE"
  --network-type tap
)

# Add optional arguments
[[ -n "$CACHE_VOLUME" ]] && LAUNCH_ARGS+=(--cache-volume "$CACHE_VOLUME")
[[ "$FOREGROUND" == "true" ]] && LAUNCH_ARGS+=(--foreground)
[[ "$MEMORY" != "1536G" ]] && LAUNCH_ARGS+=(--mem "$MEMORY")
[[ "$VCPUS" != "24" ]] && LAUNCH_ARGS+=(--vcpus "$VCPUS")
[[ "$GPU_MMIO_MB" != "262144" ]] && LAUNCH_ARGS+=(--gpu-mmio-mb "$GPU_MMIO_MB")
[[ "$PCI_HOLE_BASE_GB" != "2048" ]] && LAUNCH_ARGS+=(--pci-hole-base-gb "$PCI_HOLE_BASE_GB")

./run-vm.sh "${LAUNCH_ARGS[@]}"

echo ""
echo "=== TEE VM Deployed Successfully ==="
echo ""
echo "VM: $HOSTNAME ($VM_IP)"
echo "Access: ssh -p 2222 root@$(curl -s ifconfig.me 2>/dev/null || echo '<host-ip>')"
echo ""
echo "Management:"
echo "  Status: ./run-vm.sh --status"
echo "  Clean:  $0 --clean"

exit 0