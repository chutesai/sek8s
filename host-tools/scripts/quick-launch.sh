#!/bin/bash
# quick-launch-tee.sh - TEE VM orchestration with clean YAML parsing
# Uses Python for YAML parsing, shell for orchestration

set -e

# --------------------------------------------------------------------
# VM base image version - must match tdx-guest.qcow2 from https://vm.chutes.ai
# Update this when publishing a new VM; ensures QEMU args match VM version (RTMR0 consistency)
# --------------------------------------------------------------------
EXPECTED_BASE_SHA256="ecc1d58a4f870ff11bad5f6e309d436bd6d0a66d7a69f68cfd443ed101d81cae"

# --------------------------------------------------------------------
# Hard-coded defaults (lowest precedence)
# --------------------------------------------------------------------
CONFIG_FILE=""

HOSTNAME=""
BASE_IMAGE=""
OVERLAY_DIR=""
MINER_SS58=""
MINER_SEED=""

VM_IP="192.168.100.2"
BRIDGE_IP="192.168.100.1/24"
VM_DNS="8.8.8.8"
PUBLIC_IFACE="ens9f0np0"
CACHE_SIZE="5000G"
CACHE_VOLUME=""
STORAGE_SIZE="500G"
STORAGE_VOLUME=""
CONFIG_VOLUME=""
SKIP_BIND="false"
FOREGROUND="false"
SKIP_CHECKSUM="false"
SSH_PORT=2222
NETWORK_TYPE="tap"
EPHEMERAL="false"

# --------------------------------------------------------------------
# Temporary CLI containers
# --------------------------------------------------------------------
CLI_HOSTNAME=""
CLI_BASE_IMAGE=""
CLI_OVERLAY_DIR=""
CLI_MINER_SS58=""
CLI_MINER_SEED=""
CLI_VM_IP=""
CLI_BRIDGE_IP=""
CLI_VM_DNS=""
CLI_PUBLIC_IFACE=""
CLI_CACHE_SIZE=""
CLI_CACHE_VOLUME=""
CLI_STORAGE_SIZE=""
CLI_STORAGE_VOLUME=""
CLI_CONFIG_VOLUME=""
CLI_SKIP_BIND=""
CLI_FOREGROUND=""
CLI_SKIP_CHECKSUM=""
CLI_SSH_PORT=""
CLI_NETWORK_TYPE=""
CLI_EPHEMERAL=""
CLI_DOWNLOAD=""

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
    --base-image) CLI_BASE_IMAGE="$2"; shift 2 ;;
    --overlay-dir) CLI_OVERLAY_DIR="$2"; shift 2 ;;
    --miner-ss58) CLI_MINER_SS58="$2"; shift 2 ;;
    --miner-seed) CLI_MINER_SEED="$2"; shift 2 ;;
    --vm-ip) CLI_VM_IP="$2"; shift 2 ;;
    --bridge-ip) CLI_BRIDGE_IP="$2"; shift 2 ;;
    --vm-dns) CLI_VM_DNS="$2"; shift 2 ;;
    --public-iface) CLI_PUBLIC_IFACE="$2"; shift 2 ;;
    --cache-size) CLI_CACHE_SIZE="$2"; shift 2 ;;
    --cache-volume) CLI_CACHE_VOLUME="$2"; shift 2 ;;
    --storage-size) CLI_STORAGE_SIZE="$2"; shift 2 ;;
    --storage-volume) CLI_STORAGE_VOLUME="$2"; shift 2 ;;
    --config-volume) CLI_CONFIG_VOLUME="$2"; shift 2 ;;
    --skip-bind) CLI_SKIP_BIND="true"; shift ;;
    --foreground) CLI_FOREGROUND="true"; shift ;;
    --skip-checksum) CLI_SKIP_CHECKSUM="true"; shift ;;
    --ssh-port) CLI_SSH_PORT="$2"; shift 2 ;;
    --network-type) CLI_NETWORK_TYPE="$2"; shift 2 ;;
    --ephemeral) CLI_EPHEMERAL="true"; shift ;;
    --download)
      echo "=== Downloading VM Base Image (production) ==="
      BASE_DOWNLOAD_DIR="/var/lib/chutes/base-images"
      BASE_DOWNLOAD_PATH="$BASE_DOWNLOAD_DIR/tdx-guest.qcow2"
      sudo mkdir -p "$BASE_DOWNLOAD_DIR"
      if command -v aria2c >/dev/null 2>&1; then
        echo "Downloading to $BASE_DOWNLOAD_PATH..."
        # aria2c -o treats paths as relative to -d; use -d for dir and -o for filename only
        aria2c -x 16 -s 16 -k 1M -d "$BASE_DOWNLOAD_DIR" -o "tdx-guest.qcow2" "https://vm.chutes.ai/tdx-guest.qcow2" || {
          echo "Download failed. Ensure aria2c is installed and the URL is accessible."
          exit 1
        }
        echo "✓ Download complete: $BASE_DOWNLOAD_PATH"
      else
        echo "Error: aria2c not found. Install with: sudo apt install aria2"
        exit 1
      fi
      exit 0
      ;;

    --download-debug)
      echo "=== Downloading VM Base Image (debug) ==="
      BASE_DOWNLOAD_DIR="/var/lib/chutes/base-images"
      BASE_DOWNLOAD_PATH="$BASE_DOWNLOAD_DIR/tdx-guest-debug.qcow2"
      sudo mkdir -p "$BASE_DOWNLOAD_DIR"
      if command -v aria2c >/dev/null 2>&1; then
        echo "Downloading to $BASE_DOWNLOAD_PATH..."
        aria2c -x 16 -s 16 -k 1M -d "$BASE_DOWNLOAD_DIR" -o "tdx-guest-debug.qcow2" "https://vm.chutes.ai/tdx-guest-debug.qcow2" || {
          echo "Download failed. Ensure aria2c is installed and the URL is accessible."
          exit 1
        }
        echo "✓ Download complete: $BASE_DOWNLOAD_PATH"
      else
        echo "Error: aria2c not found. Install with: sudo apt install aria2"
        exit 1
      fi
      exit 0
      ;;

    --clean)
      echo "=== Cleaning Up TEE VM Environment ==="
      if [[ -x "./run-td" ]]; then
        echo "Stopping Chutes VM (if running)..."
        ./run-td --clean 2>/dev/null || true
      fi

      echo "Waiting for VM processes to exit..."
      for i in {1..15}; do
        if ! pgrep -f 'qemu-system|qemu-kvm|run-td' >/dev/null 2>&1; then
          echo "No VM processes found. Proceeding with bridge cleanup."
          break
        fi
        echo "VM processes still running; waiting... ($i/15)"
        sleep 1
      done

      ./setup-bridge.sh --clean 2>/dev/null || true
      exit 0
      ;;

    --template)
      cp config/config.tmpl.yaml config.yaml
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
  --base-image PATH         Path to base VM image (qcow2). Default: /var/lib/chutes/base-images/tdx-guest.qcow2
  --overlay-dir PATH        Directory for overlay files. Default: /var/lib/chutes/vm-overlays/
  --miner-ss58 VALUE        Miner SS58 credential (required)
  --miner-seed VALUE        Miner seed credential (required)

Network:
  --vm-ip IP
  --bridge-ip IP/CIDR
  --vm-dns DNS
  --public-iface IFACE

Volumes:
  --cache-size SIZE
  --cache-volume PATH        Default: cache-<hostname>.raw (existing .qcow2 allowed at launch)
  --storage-size SIZE
  --storage-volume PATH      Default: storage-<hostname>.raw (existing .qcow2 allowed at launch)
  --config-volume PATH
  --skip-bind
  --skip-checksum         Skip base image SHA256 verification (for debug with custom images)

Runtime:
  --foreground
  --network-type [tap|user]
  --ephemeral               Use ephemeral overlay (cleared on reboot)

Resource sizing is fixed inside run-td to preserve RTMR determinism.

Management:
  --clean                   Clean up VM and bridge
  --download                Download VM base image (production) to /var/lib/chutes/base-images/
  --download-debug          Download VM debug image to /var/lib/chutes/base-images/

Examples:
  # Create template config
  $0 --template

  # Use config file
  $0 config.yaml

  # Use config with overrides
  $0 config.yaml --foreground --skip-bind

  # Download VM base image (before first run)
  $0 --download
  $0 --download-debug        # Debug image (SSH, no encryption)

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

  if [[ ! -d "./chutes_host" ]]; then
    echo "Error: chutes_host package not found in current directory"
    exit 1
  fi

  set +e
  CONFIG_OUTPUT=$(python3 -m chutes_host.config "$CONFIG_FILE" 2>&1)
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
[[ -n "$CLI_BASE_IMAGE" ]] && BASE_IMAGE="$CLI_BASE_IMAGE"
[[ -n "$CLI_OVERLAY_DIR" ]] && OVERLAY_DIR="$CLI_OVERLAY_DIR"
[[ -n "$CLI_MINER_SS58" ]] && MINER_SS58="$CLI_MINER_SS58"
[[ -n "$CLI_MINER_SEED" ]] && MINER_SEED="$CLI_MINER_SEED"

[[ -n "$CLI_VM_IP" ]] && VM_IP="$CLI_VM_IP"
[[ -n "$CLI_BRIDGE_IP" ]] && BRIDGE_IP="$CLI_BRIDGE_IP"
[[ -n "$CLI_VM_DNS" ]] && VM_DNS="$CLI_VM_DNS"
[[ -n "$CLI_PUBLIC_IFACE" ]] && PUBLIC_IFACE="$CLI_PUBLIC_IFACE"

[[ -n "$CLI_CACHE_SIZE" ]] && CACHE_SIZE="$CLI_CACHE_SIZE"
[[ -n "$CLI_CACHE_VOLUME" ]] && CACHE_VOLUME="$CLI_CACHE_VOLUME"
[[ -n "$CLI_STORAGE_SIZE" ]] && STORAGE_SIZE="$CLI_STORAGE_SIZE"
[[ -n "$CLI_STORAGE_VOLUME" ]] && STORAGE_VOLUME="$CLI_STORAGE_VOLUME"
[[ -n "$CLI_CONFIG_VOLUME" ]] && CONFIG_VOLUME="$CLI_CONFIG_VOLUME"

[[ -n "$CLI_SKIP_BIND" ]] && SKIP_BIND="$CLI_SKIP_BIND"
[[ -n "$CLI_FOREGROUND" ]] && FOREGROUND="$CLI_FOREGROUND"
[[ -n "$CLI_SKIP_CHECKSUM" ]] && SKIP_CHECKSUM="true"

[[ -n "$CLI_SSH_PORT" ]] && SSH_PORT="$CLI_SSH_PORT"
[[ -n "$CLI_NETWORK_TYPE" ]] && NETWORK_TYPE="$CLI_NETWORK_TYPE"
[[ -n "$CLI_EPHEMERAL" ]] && EPHEMERAL="$CLI_EPHEMERAL"

# Default base image and overlay directory when not specified
[[ -z "$BASE_IMAGE" ]] && BASE_IMAGE="/var/lib/chutes/base-images/tdx-guest.qcow2"
if [[ "$EPHEMERAL" == "true" ]]; then
  OVERLAY_DIR="/tmp/chutes-vm-overlays"
elif [[ -z "$OVERLAY_DIR" ]]; then
  OVERLAY_DIR="/var/lib/chutes/vm-overlays"
fi

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
  CACHE_VOLUME="cache-${HOSTNAME}.raw"
fi

if [[ -z "$STORAGE_VOLUME" ]]; then
  STORAGE_VOLUME="storage-${HOSTNAME}.raw"
fi

echo ""
echo "=== TEE VM Orchestration ==="
echo "Config source: ${CONFIG_FILE:-command line only}"
echo "Hostname: $HOSTNAME"
echo "Base image: $BASE_IMAGE"
echo "Overlay dir: $OVERLAY_DIR"
echo "VM IP: $VM_IP"
echo "Bridge IP: $BRIDGE_IP"
echo "Cache volume: $CACHE_VOLUME ($CACHE_SIZE)"
echo "Storage volume: $STORAGE_VOLUME ($STORAGE_SIZE)"
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

# Ensure NUMA zone reclaim is disabled (allows cross-node allocation for QEMU/KVM)
ZONE_RECLAIM=$(sysctl -n vm.zone_reclaim_mode 2>/dev/null || echo "unknown")
if [[ "$ZONE_RECLAIM" != "0" ]]; then
  echo "⚠ vm.zone_reclaim_mode=$ZONE_RECLAIM (should be 0 for TDX VM workloads)"
  echo "  Fixing: sysctl -w vm.zone_reclaim_mode=0"
  sudo sysctl -w vm.zone_reclaim_mode=0
  echo "  To make persistent: echo 'vm.zone_reclaim_mode=0' >> /etc/sysctl.d/99-numa.conf"
fi
echo "✓ NUMA zone reclaim disabled (vm.zone_reclaim_mode=0)"

echo "✓ Host configuration verified"
echo ""

# --------------------------------------------------------------------
# Device binding is now handled by run-td script
# --------------------------------------------------------------------
# Note: Device binding to vfio-pci is now done inside prepare_gpus()
# in the run-td script, so we no longer need to call bind.sh separately
echo ""


# --------------------------------------------------------------------
# Cache volume (required)
# --------------------------------------------------------------------
echo "Step 2: Preparing cache volume..."
if [[ -z "$CACHE_VOLUME" ]]; then
  echo "✗ Error: CACHE_VOLUME is unset"
  exit 1
fi

if [[ -f "$CACHE_VOLUME" ]] || [[ -b "$CACHE_VOLUME" ]]; then
  echo "✓ Using existing cache volume: $CACHE_VOLUME"
else
  if [[ "$CACHE_VOLUME" == *.qcow2 ]]; then
    echo "✗ Error: qcow2 volumes cannot be created. Use .raw for new volumes (e.g. cache-${HOSTNAME}.raw)"
    echo "  Existing qcow2 volumes can still be used if they already exist."
    exit 1
  fi
  echo "Creating cache volume at: $CACHE_VOLUME ($CACHE_SIZE)"
  if sudo ./volumes/create-cache.sh "$CACHE_VOLUME" "$CACHE_SIZE" "tdx-cache"; then
    echo "✓ Cache volume created"
  else
    echo "✗ Error: Failed to create cache volume at $CACHE_VOLUME"
    exit 1
  fi
fi
echo ""

# --------------------------------------------------------------------
# Storage volume (required for VM storage - used for containerd and kubelet-pods)
# --------------------------------------------------------------------
echo "Step 3: Preparing storage volume..."
if [[ -z "$STORAGE_VOLUME" ]]; then
  echo "✗ Error: STORAGE_VOLUME is unset"
  echo "  Storage volume is required for VM storage (containerd and kubelet-pods)"
  exit 1
fi

if [[ -f "$STORAGE_VOLUME" ]] || [[ -b "$STORAGE_VOLUME" ]]; then
  echo "✓ Using existing storage volume: $STORAGE_VOLUME"
else
  if [[ "$STORAGE_VOLUME" == *.qcow2 ]]; then
    echo "✗ Error: qcow2 volumes cannot be created. Use .raw for new volumes (e.g. storage-${HOSTNAME}.raw)"
    echo "  Existing qcow2 volumes can still be used if they already exist."
    exit 1
  fi
  echo "Creating storage volume at: $STORAGE_VOLUME ($STORAGE_SIZE)"
  if sudo ./volumes/create-cache.sh "$STORAGE_VOLUME" "$STORAGE_SIZE" "storage"; then
    echo "✓ Storage volume created"
  else
    echo "✗ Error: Failed to create storage volume at $STORAGE_VOLUME"
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
    if sudo ./volumes/create-config.sh "$CONFIG_VOLUME" "$HOSTNAME" "$MINER_SS58" "$MINER_SEED" "$VM_IP" "${BRIDGE_IP%/*}" "$VM_DNS"; then
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
  if sudo ./volumes/create-config.sh "$CONFIG_VOLUME" "$HOSTNAME" "$MINER_SS58" "$MINER_SEED" "$VM_IP" "${BRIDGE_IP%/*}" "$VM_DNS"; then
    echo "✓ Config volume created"
  else
    echo "✗ Error: Failed to create config volume at $CONFIG_VOLUME"
    exit 1
  fi
fi
echo ""

# --------------------------------------------------------------------
# Step 4b: Prepare VM image (verify base SHA256, create/reuse overlay)
# --------------------------------------------------------------------
echo "Step 4b: Preparing VM image (verify + overlay)..."
# Use tail -1 to extract only the path; qemu-img create may write "Formatting '...'" to stderr
# which can be captured when streams are merged (e.g. in some environments)
SKIP_ARG=""
[[ "$SKIP_CHECKSUM" == "true" ]] && SKIP_ARG="1"
OVERLAY_IMAGE=$(./prepare-vm-image.sh "$BASE_IMAGE" "$HOSTNAME" "$EXPECTED_BASE_SHA256" "$OVERLAY_DIR" $SKIP_ARG | tail -1)
# Pipeline masks exit status; PIPESTATUS[0] is prepare-vm-image's exit code
[[ ${PIPESTATUS[0]} -ne 0 ]] && { echo "Error: VM image preparation failed (see output above)"; exit 1; }
[[ -z "$OVERLAY_IMAGE" ]] && { echo "Error: Failed to get overlay image path"; exit 1; }
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
    --public-iface "$PUBLIC_IFACE" \
    --multi-queue )

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
  --image "$OVERLAY_IMAGE"
  --config-volume "$CONFIG_VOLUME"
  --network-type "$NETWORK_TYPE"
)

if [[ "$NETWORK_TYPE" == "tap" ]]; then
  LAUNCH_ARGS+=(--net-iface "$NET_IFACE")
fi

# Additional args
LAUNCH_ARGS+=(--cache-volume "$CACHE_VOLUME")
LAUNCH_ARGS+=(--storage-volume "$STORAGE_VOLUME")
[[ "$FOREGROUND" == "true" ]] && LAUNCH_ARGS+=(--foreground)

# Call Python runner
python3 ./run-td "${LAUNCH_ARGS[@]}"

echo ""
echo "=== Chutes VM Deployed Successfully ==="
echo ""

exit 0
