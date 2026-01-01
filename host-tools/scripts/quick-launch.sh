#!/bin/bash
# quick-launch-tee.sh - TEE VM orchestration with clean YAML parsing
# Uses Python for YAML parsing, shell for orchestration

set -e

# --------------------------------------------------------------------
# Hard-coded defaults (lowest precedence)
# --------------------------------------------------------------------
CONFIG_FILE=""

HOSTNAME=""
VM_IMAGE=""
MINER_SS58=""
MINER_SEED=""

VM_IP="192.168.100.2"
BRIDGE_IP="192.168.100.1/24"
VM_DNS="8.8.8.8"
PUBLIC_IFACE="ens9f0np0"
CACHE_SIZE="5000G"
CACHE_VOLUME=""
CONTAINERD_SIZE="500G"
CONTAINERD_VOLUME=""
CONFIG_VOLUME=""
SKIP_BIND="false"
FOREGROUND="false"
SSH_PORT=2222
NETWORK_TYPE="tap"

# --------------------------------------------------------------------
# Temporary CLI containers
# --------------------------------------------------------------------
CLI_HOSTNAME=""
CLI_VM_IMAGE=""
CLI_MINER_SS58=""
CLI_MINER_SEED=""
CLI_VM_IP=""
CLI_BRIDGE_IP=""
CLI_VM_DNS=""
CLI_PUBLIC_IFACE=""
CLI_CACHE_SIZE=""
CLI_CACHE_VOLUME=""
CLI_CONTAINERD_SIZE=""
CLI_CONTAINERD_VOLUME=""
CLI_CONFIG_VOLUME=""
CLI_SKIP_BIND=""
CLI_FOREGROUND=""
CLI_SSH_PORT=""
CLI_NETWORK_TYPE=""

# --------------------------------------------------------------------
# Parse CLI options
# --------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case $1 in
    *.yaml|*.yml)
      CONFIG_FILE="$1"
      shift
      ;;
    --config) CONFIG_FILE="$2"; shift 2 ;;
    --hostname) CLI_HOSTNAME="$2"; shift 2 ;;
    --image) CLI_VM_IMAGE="$2"; shift 2 ;;
    --miner-ss58) CLI_MINER_SS58="$2"; shift 2 ;;
    --miner-seed) CLI_MINER_SEED="$2"; shift 2 ;;
    --vm-ip) CLI_VM_IP="$2"; shift 2 ;;
    --bridge-ip) CLI_BRIDGE_IP="$2"; shift 2 ;;
    --vm-dns) CLI_VM_DNS="$2"; shift 2 ;;
    --public-iface) CLI_PUBLIC_IFACE="$2"; shift 2 ;;
    --cache-size) CLI_CACHE_SIZE="$2"; shift 2 ;;
    --cache-volume) CLI_CACHE_VOLUME="$2"; shift 2 ;;
    --containerd-size) CLI_CONTAINERD_SIZE="$2"; shift 2 ;;
    --containerd-volume) CLI_CONTAINERD_VOLUME="$2"; shift 2 ;;
    --config-volume) CLI_CONFIG_VOLUME="$2"; shift 2 ;;
    --skip-bind) CLI_SKIP_BIND="true"; shift ;;
    --foreground) CLI_FOREGROUND="true"; shift ;;
    --ssh-port) CLI_SSH_PORT="$2"; shift 2 ;;
    --network-type) CLI_NETWORK_TYPE="$2"; shift 2 ;;

    --clean)
      echo "=== Cleaning Up TEE VM Environment ==="
      # Ensure the Chutes VM is stopped before attempting to unbind passthrough devices.
      # Some runtimes may take a short moment to release devices, so stop the VM
      # first and then wait for VM-related processes to exit.
      if [[ -x "./run-td" ]]; then
        echo "Stopping Chutes VM (if running)..."
        ./run-td --clean 2>/dev/null || true
      fi

      # Give the VM a short window to exit and release devices.
      echo "Waiting for VM processes to exit before unbinding devices..."
      for i in {1..15}; do
        # Look for common VM process names. Adjust pattern if your VM runtime differs.
        if ! pgrep -f 'qemu-system|qemu-kvm|run-td' >/dev/null 2>&1; then
          echo "No VM processes found. Proceeding with bridge cleanup and device unbind."
          break
        fi
        echo "VM processes still running; waiting... ($i/15)"
        sleep 1
      done

      # Clean up networking/bridge
      ./setup-bridge.sh --clean 2>/dev/null || true

      # Only unbind devices once the VM has stopped (or timeout reached).
      if [ -f "./unbind.sh" ]; then
        echo "Unbinding passthrough devices..."
        sudo ./unbind.sh 2>/dev/null || true
      fi
      exit 0
      ;;

    --template)
      cp config.tmpl.yaml config.yaml
      echo "Created config.yaml"
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

Command Line Options (CLI overrides YAML when provided):
  --hostname NAME           VM hostname (required if not in YAML)
  --image PATH              Path to VM image (overrides YAML and CHUTES_IMAGE env)
  --miner-ss58 VALUE        Miner SS58 credential (required)
  --miner-seed VALUE        Miner seed credential (required)

Network:
  --vm-ip IP
  --bridge-ip IP/CIDR
  --vm-dns DNS
  --public-iface IFACE

Volumes:
  --cache-size SIZE
  --cache-volume PATH
  --containerd-size SIZE
  --containerd-volume PATH
  --skip-bind

Runtime:
  --foreground
  --network-type [tap|user]

Resource sizing is fixed inside run-td to preserve RTMR determinism.

Management:
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
EOF
      exit 0
      ;;

    *)
      echo "Unknown option: $1. Use --help for usage."
      exit 1
      ;;
  esac
done

# --------------------------------------------------------------------
# Load configuration file (YAML) – overrides defaults
# --------------------------------------------------------------------
if [[ -n "$CONFIG_FILE" ]]; then
  echo "Loading configuration from: $CONFIG_FILE"

  if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python 3 not found. Install with: sudo apt install python3"
    exit 1
  fi

  if ! python3 -c "import yaml" 2>/dev/null; then
    echo "Error: PyYAML not found. Install with: pip3 install pyyaml"
    exit 1
  fi

  if [[ ! -f "./parse-config.py" ]]; then
    echo "Error: parse-config.py not found in current directory"
    exit 1
  fi

  set +e
  CONFIG_OUTPUT=$(python3 ./parse-config.py "$CONFIG_FILE" 2>&1)
  CONFIG_EXIT_CODE=$?
  set -e

  if [[ $CONFIG_EXIT_CODE -ne 0 ]]; then
    echo "Error parsing config file:"
    echo "$CONFIG_OUTPUT"
    exit 1
  fi

  # This sets HOSTNAME, MINER_SS58, etc. from YAML
  eval "$CONFIG_OUTPUT"
  echo "✓ Configuration loaded successfully"
fi

# --------------------------------------------------------------------
# Apply CLI overrides (highest precedence)
# --------------------------------------------------------------------
[[ -n "$CLI_HOSTNAME" ]] && HOSTNAME="$CLI_HOSTNAME"
[[ -n "$CLI_VM_IMAGE" ]] && VM_IMAGE="$CLI_VM_IMAGE"
[[ -n "$CLI_MINER_SS58" ]] && MINER_SS58="$CLI_MINER_SS58"
[[ -n "$CLI_MINER_SEED" ]] && MINER_SEED="$CLI_MINER_SEED"

[[ -n "$CLI_VM_IP" ]] && VM_IP="$CLI_VM_IP"
[[ -n "$CLI_BRIDGE_IP" ]] && BRIDGE_IP="$CLI_BRIDGE_IP"
[[ -n "$CLI_VM_DNS" ]] && VM_DNS="$CLI_VM_DNS"
[[ -n "$CLI_PUBLIC_IFACE" ]] && PUBLIC_IFACE="$CLI_PUBLIC_IFACE"

[[ -n "$CLI_CACHE_SIZE" ]] && CACHE_SIZE="$CLI_CACHE_SIZE"
[[ -n "$CLI_CACHE_VOLUME" ]] && CACHE_VOLUME="$CLI_CACHE_VOLUME"
[[ -n "$CLI_CONTAINERD_SIZE" ]] && CONTAINERD_SIZE="$CLI_CONTAINERD_SIZE"
[[ -n "$CLI_CONTAINERD_VOLUME" ]] && CONTAINERD_VOLUME="$CLI_CONTAINERD_VOLUME"
[[ -n "$CLI_CONFIG_VOLUME" ]] && CONFIG_VOLUME="$CLI_CONFIG_VOLUME"

[[ -n "$CLI_SKIP_BIND" ]] && SKIP_BIND="$CLI_SKIP_BIND"
[[ -n "$CLI_FOREGROUND" ]] && FOREGROUND="$CLI_FOREGROUND"

[[ -n "$CLI_SSH_PORT" ]] && SSH_PORT="$CLI_SSH_PORT" 
[[ -n "$CLI_NETWORK_TYPE" ]] && NETWORK_TYPE="$CLI_NETWORK_TYPE"

# Validate network type
if [[ "$NETWORK_TYPE" != "tap" && "$NETWORK_TYPE" != "user" ]]; then
  echo "Error: --network-type must be 'tap' or 'user'"
  exit 1
fi

# --------------------------------------------------------------------
# Validate required parameters (must come from YAML or CLI)
# --------------------------------------------------------------------
if [[ -z "$HOSTNAME" || -z "$MINER_SS58" || -z "$MINER_SEED" ]]; then
  echo "Error: Missing required configuration:"
  [[ -z "$HOSTNAME" ]] && echo "  - hostname (vm.hostname or --hostname)"
  [[ -z "$MINER_SS58" ]] && echo "  - miner.ss58 (miner.ss58 or --miner-ss58)"
  [[ -z "$MINER_SEED" ]] && echo "  - miner.seed (miner.seed or --miner-seed)"
  echo ""
  echo "Provide via config file or command line, for example:"
  echo "  $0 --template        # create config.yaml template"
  echo "  $0 config.yaml       # and edit it"
  echo "or"
  echo "  $0 --hostname miner --miner-ss58 'ss58' --miner-seed 'seed'"
  exit 1
fi

if [[ -z "$CACHE_VOLUME" ]]; then
  CACHE_VOLUME="cache-${HOSTNAME}.qcow2"
fi

if [[ -z "$CONTAINERD_VOLUME" ]]; then
  CONTAINERD_VOLUME="containerd-${HOSTNAME}.qcow2"
fi

echo ""
echo "=== TEE VM Orchestration ==="
echo "Config source: ${CONFIG_FILE:-command line only}"
echo "Hostname: $HOSTNAME"
echo "Image: ${VM_IMAGE:-<from CHUTES_IMAGE env or run-td default>}"
echo "VM IP: $VM_IP"
echo "Bridge IP: $BRIDGE_IP"
echo "Cache volume: $CACHE_VOLUME ($CACHE_SIZE)"
echo "Containerd volume: $CONTAINERD_VOLUME ($CONTAINERD_SIZE)"
echo "Binding: $([[ "$SKIP_BIND" == "true" ]] && echo "Skipped" || echo "Enabled")"
echo "Network: $NETWORK_TYPE"
echo ""

# --------------------------------------------------------------------
# Step 0: Verify host configuration
# --------------------------------------------------------------------
echo "Step 0: Verifying host configuration..."

# Check if TDX module is initialized via dmesg
TDX_DMESG=$(sudo dmesg | grep -i tdx 2>/dev/null || echo "")

if ! echo "$TDX_DMESG" | grep -q "module initialized"; then
  echo "✗ Error: TDX module not initialized on this host"
  echo ""
  echo "TDX-related kernel messages:"
  if [[ -n "$TDX_DMESG" ]]; then
    echo "$TDX_DMESG" | tail -n 10
  else
    echo "  (none found)"
  fi
  echo ""
  echo "To enable TDX:"
  echo "  1. Verify CPU supports TDX: grep tdx /proc/cpuinfo"
  echo "  2. Enable TDX in BIOS/UEFI settings"
  echo "  3. Ensure TDX kernel support is installed"
  echo "  4. Reboot and check: dmesg | grep -i tdx"
  exit 1
fi

# Additionally check CPU support
if ! grep -q tdx /proc/cpuinfo 2>/dev/null; then
  echo "⚠ Warning: TDX instruction not found in /proc/cpuinfo"
  echo "  This may indicate incomplete TDX support"
fi

echo "✓ TDX module initialized"
echo "✓ Host TDX configuration verified"
echo ""

# --------------------------------------------------------------------
# Bind devices for passthrough
# --------------------------------------------------------------------
if [[ "$SKIP_BIND" != "true" ]]; then
  echo "Step 1: Binding NVIDIA devices to vfio-pci..."
  if [[ -f "./bind.sh" ]]; then
    sudo ./bind.sh
    echo "✓ Device binding complete"
  else
    echo "Error: bind.sh not found in $(pwd)"
    exit 1
  fi
else
  echo "Step 1: Skipping device binding (--skip-bind set)"
fi
echo ""


# --------------------------------------------------------------------
# Cache volume (required)
# --------------------------------------------------------------------
echo "Step 2: Preparing cache volume..."
if [[ -z "$CACHE_VOLUME" ]]; then
  echo "✗ Error: CACHE_VOLUME is unset"
  exit 1
fi

if [[ -f "$CACHE_VOLUME" ]]; then
  echo "✓ Using existing cache volume: $CACHE_VOLUME"
else
  echo "Creating cache volume at: $CACHE_VOLUME ($CACHE_SIZE)"
  if sudo ./create-cache.sh "$CACHE_VOLUME" "$CACHE_SIZE" "tdx-cache"; then
    echo "✓ Cache volume created"
  else
    echo "✗ Error: Failed to create cache volume at $CACHE_VOLUME"
    exit 1
  fi
fi
echo ""

# --------------------------------------------------------------------
# Containerd cache volume (required for encrypted containerd storage)
# --------------------------------------------------------------------
echo "Step 3: Preparing containerd cache volume..."
if [[ -z "$CONTAINERD_VOLUME" ]]; then
  echo "✗ Error: CONTAINERD_VOLUME is unset"
  exit 1
fi

if [[ -f "$CONTAINERD_VOLUME" ]]; then
  echo "✓ Using existing containerd volume: $CONTAINERD_VOLUME"
else
  echo "Creating containerd volume at: $CONTAINERD_VOLUME ($CONTAINERD_SIZE)"
  if sudo ./create-cache.sh "$CONTAINERD_VOLUME" "$CONTAINERD_SIZE" "containerd-cache"; then
    echo "✓ Containerd volume created"
  else
    echo "✗ Error: Failed to create containerd volume at $CONTAINERD_VOLUME"
    exit 1
  fi
fi
echo ""

# --------------------------------------------------------------------
# Config volume
# --------------------------------------------------------------------
echo "Step 4: Setting up config volume..."
if [[ -n "$CONFIG_VOLUME" ]]; then
  if [[ -f "$CONFIG_VOLUME" ]]; then
    echo "✓ Using existing config volume: $CONFIG_VOLUME"
  else
    echo "Creating config volume at configured path: $CONFIG_VOLUME"
    if sudo ./create-config.sh "$CONFIG_VOLUME" "$HOSTNAME" "$MINER_SS58" "$MINER_SEED" "$VM_IP" "${BRIDGE_IP%/*}" "$VM_DNS"; then
      echo "✓ Config volume created"
    else
      echo "✗ Error: Failed to create config volume at $CONFIG_VOLUME"
      exit 1
    fi
  fi
else
  CONFIG_VOLUME="config-${HOSTNAME}.qcow2"
  [[ -f "$CONFIG_VOLUME" ]] && sudo rm -f "$CONFIG_VOLUME"

  echo "Creating config volume: $CONFIG_VOLUME"
  if sudo ./create-config.sh "$CONFIG_VOLUME" "$HOSTNAME" "$MINER_SS58" "$MINER_SEED" "$VM_IP" "${BRIDGE_IP%/*}" "$VM_DNS"; then
    echo "✓ Config volume created"
  else
    echo "✗ Error: Failed to create config volume at $CONFIG_VOLUME"
    exit 1
  fi
fi
echo ""

# --------------------------------------------------------------------
# Bridge networking
# --------------------------------------------------------------------
NET_IFACE=""
if [[ "$NETWORK_TYPE" == "tap" ]]; then
  echo "Step 5: Setting up bridge networking..."
  BRIDGE_OUTPUT=$(./setup-bridge.sh \
    --bridge-ip "$BRIDGE_IP" \
    --vm-ip "${VM_IP}/24" \
    --vm-dns "$VM_DNS" \
    --public-iface "$PUBLIC_IFACE" )

  NET_IFACE=$(echo "$BRIDGE_OUTPUT" | grep "Network interface:" | awk '{print $3}')
  if [[ -z "$NET_IFACE" ]]; then
    echo "Error: Failed to extract TAP interface"
    echo "$BRIDGE_OUTPUT"
    exit 1
  fi
  echo "✓ Bridge configured (TAP: $NET_IFACE)"
  echo ""
else
  echo "Step 5: Skipping bridge setup (network-type=user)"
  echo ""
fi

# --------------------------------------------------------------------
# Launch VM
# --------------------------------------------------------------------
echo "Launching Chutes VM..."

LAUNCH_ARGS=(
  --pass-gpus
  --config-volume "$CONFIG_VOLUME"
  --network-type "$NETWORK_TYPE"
)

# Add image if specified in config or CLI
if [[ -n "$VM_IMAGE" ]]; then
  LAUNCH_ARGS+=(--image "$VM_IMAGE")
fi

if [[ "$NETWORK_TYPE" == "tap" ]]; then
  LAUNCH_ARGS+=(--net-iface "$NET_IFACE")
fi

# Additional args
LAUNCH_ARGS+=(--cache-volume "$CACHE_VOLUME")
LAUNCH_ARGS+=(--containerd-volume "$CONTAINERD_VOLUME")
[[ "$FOREGROUND" == "true" ]] && LAUNCH_ARGS+=(--foreground)

# Call Python runner
python3 ./run-td "${LAUNCH_ARGS[@]}"

echo ""
echo "=== Chutes VM Deployed Successfully ==="
echo ""

exit 0
