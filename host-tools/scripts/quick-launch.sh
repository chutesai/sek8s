#!/bin/bash
# quick-launch-tee.sh - TEE VM orchestration with clean YAML parsing
# Uses Python for YAML parsing, shell for orchestration

set -e

run_create_config() {
  local vol_path="$1"
  if [[ -n "$DOCKER_HUB_USERNAME" && -n "$DOCKER_HUB_TOKEN" ]]; then
    sudo ./volumes/create-config.sh "$vol_path" "$HOSTNAME" "$MINER_SS58" "$MINER_SEED" "$VM_IP" "${BRIDGE_IP%/*}" "$VM_DNS" "$DOCKER_HUB_USERNAME" "$DOCKER_HUB_TOKEN"
  else
    sudo ./volumes/create-config.sh "$vol_path" "$HOSTNAME" "$MINER_SS58" "$MINER_SEED" "$VM_IP" "${BRIDGE_IP%/*}" "$VM_DNS"
  fi
}

# Download a full image set (1.4.0+) into its own per-variant directory: the qcow2, the
# direct-boot kernel/initrd/cmdline OVMF boots directly, and the manifest that ties them
# together. The set lives in one directory so the launcher resolves the qcow2 + sidecars
# next to each other, and `image_set resolve --full` verifies every downloaded byte
# against the manifest (the R2-published integrity source).
#   $1 = image basename (tdx-guest | tdx-guest-debug)
download_image_set() {
  local base="$1" ext
  local dir="/var/lib/chutes/base-images/${base}"
  sudo mkdir -p "$dir"

  # Download into a fixed per-variant directory (overwrites in place — keep an old build
  # by moving its directory aside before re-downloading). Manifest last so a partial
  # download never leaves a manifest advertising bytes that aren't there yet.
  echo "Downloading ${base}.qcow2..."
  aria2c -x 16 -s 16 -k 1M --allow-overwrite=true -d "$dir" -o "${base}.qcow2" \
    "https://vm.chutes.ai/${base}.qcow2" || { echo "Download failed for ${base}.qcow2"; exit 1; }
  for ext in vmlinuz initrd cmdline; do
    echo "Downloading ${base}.${ext} (direct-boot artifact)..."
    aria2c -x 16 -s 16 -k 1M --allow-overwrite=true -d "$dir" -o "${base}.${ext}" \
      "https://vm.chutes.ai/${base}.${ext}" || {
      echo "Download failed for ${base}.${ext}. It must be published alongside the qcow2 (1.4.0+)."
      exit 1
    }
  done
  echo "Downloading manifest.json (coherence contract)..."
  aria2c -x 16 -s 16 -k 1M --allow-overwrite=true -d "$dir" -o "manifest.json" \
    "https://vm.chutes.ai/${base}.manifest.json" || {
    echo "Download failed for ${base}.manifest.json. It must be published alongside the qcow2 (1.4.0+)."
    exit 1
  }

  echo "Verifying the downloaded image set against its manifest..."
  python3 -m chutes.guest.image_set resolve --full "$dir" >/dev/null || {
    echo "ERROR: downloaded image set failed manifest verification (see above)."
    exit 1
  }
  echo "✓ Image set downloaded and verified: $dir"
  echo "  Point base_image at this directory (or leave it empty to use the default)."
}

# Integrity is carried entirely by the per-image-set manifest.json (verified at download
# and launch by chutes.guest.image_set) — there is no pinned base-image hash to maintain.

# --------------------------------------------------------------------
# Hard-coded defaults (lowest precedence)
# --------------------------------------------------------------------
CONFIG_FILE=""

HOSTNAME=""
BASE_IMAGE=""
VM_IMAGE_DIR=""
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
SSH_PORT=2222
NETWORK_TYPE="tap"
EPHEMERAL="false"
BENCHMARK="false"
DOCKER_HUB_USERNAME=""
DOCKER_HUB_TOKEN=""

# --------------------------------------------------------------------
# Temporary CLI containers
# --------------------------------------------------------------------
CLI_HOSTNAME=""
CLI_BASE_IMAGE=""
CLI_VM_IMAGE_DIR=""
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
CLI_SSH_PORT=""
CLI_NETWORK_TYPE=""
CLI_EPHEMERAL=""
CLI_BENCHMARK=""
CLI_DOWNLOAD=""
CLI_DOCKER_HUB_USERNAME=""
CLI_DOCKER_HUB_TOKEN=""
CLI_FORCE=""
CLI_CLEAN=""

# --------------------------------------------------------------------
# Duplicate-instance guard (chutes-td QEMU must not stack without --force)
# Keep detection aligned with ansible/host/roles/chutes_tee_vm/files/is_live_chutes_td.sh.
# --------------------------------------------------------------------
_PROCESS_NAME_CHUTES_TD="chutes-td"

_live_chutes_td_qemu_running() {
  local pid state cmdline
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    [[ -r "/proc/$pid/stat" ]] || continue
    state=$(ps -p "$pid" -o stat= 2>/dev/null || echo "")
    [[ "$state" == Z* ]] && continue
    cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || echo "")
    if [[ "$cmdline" != *qemu-system* && "$cmdline" != *qemu-kvm* ]]; then
      continue
    fi
    [[ "$cmdline" == *"$_PROCESS_NAME_CHUTES_TD"* ]] || continue
    return 0
  done < <(
    { pgrep -f 'qemu-system' 2>/dev/null || true
      pgrep -f 'qemu-kvm' 2>/dev/null || true
    } | sort -un
  )
  return 1
}

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
    --vm-image-dir) CLI_VM_IMAGE_DIR="$2"; shift 2 ;;
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
    --ssh-port) CLI_SSH_PORT="$2"; shift 2 ;;
    --network-type) CLI_NETWORK_TYPE="$2"; shift 2 ;;
    --ephemeral) CLI_EPHEMERAL="true"; shift ;;
    --benchmark) CLI_BENCHMARK="true"; shift ;;
    --docker-hub-username) CLI_DOCKER_HUB_USERNAME="$2"; shift 2 ;;
    --docker-hub-token) CLI_DOCKER_HUB_TOKEN="$2"; shift 2 ;;
    --force) CLI_FORCE="true"; shift ;;
    --clean) CLI_CLEAN="true"; shift ;;
    --download)
      echo "=== Downloading VM Image Set (production) ==="
      if ! command -v aria2c >/dev/null 2>&1; then
        echo "Error: aria2c not found. Install with: sudo apt install aria2"
        exit 1
      fi
      download_image_set tdx-guest
      exit 0
      ;;

    --download-debug)
      echo "=== Downloading VM Image Set (debug) ==="
      if ! command -v aria2c >/dev/null 2>&1; then
        echo "Error: aria2c not found. Install with: sudo apt install aria2"
        exit 1
      fi
      download_image_set tdx-guest-debug
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
  --base-image PATH         Image-set directory (qcow2 + boot artifacts + manifest) or a bare .qcow2. Default: /var/lib/chutes/base-images/tdx-guest/
  --vm-image-dir PATH       Directory for per-VM image files. Default: /var/lib/chutes/vm-images/
  --miner-ss58 VALUE        Miner SS58 credential (required)
  --miner-seed VALUE        Miner seed credential (required)
  --docker-hub-username U   Docker Hub username (optional; use with --docker-hub-token; overrides config.yaml)
  --docker-hub-token T      Docker Hub PAT or password (optional; overrides config.yaml)

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
  --config-volume PATH       Existing qcow2 is repopulated from config.yaml each launch (same file)
  --skip-bind

Runtime:
  --foreground
  --network-type [tap|user]
  --ephemeral               Use ephemeral per-VM image in /tmp/ (discarded on reboot)

Host CPU tuning is a separate, operator-driven step (decoupled from launch):
  sudo chutes-tune-host      Apply NVIDIA-recommended tuning
                             (governor=performance, disable C1E/C6)
  sudo chutes-restore-host   Revert to the saved settings

Resource sizing is fixed inside run-td to preserve RTMR determinism.

Benchmark Mode:
  --benchmark               Launch in benchmark mode (no miner creds, no cache/config volume,
                            auto-installs and starts benchmark-netlog service)

Management:
  --clean                   Clean up VM, bridge, and benchmark-netlog service (if running)
  --download                Download VM base image (production) to /var/lib/chutes/base-images/
  --download-debug          Download VM debug image to /var/lib/chutes/base-images/
  --force                   Allow launch even if a chutes-td QEMU instance appears running (unsafe)

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

  # Benchmark launch (image is built on-server via Ansible, not downloaded)
  $0 --benchmark config.benchmark.yaml

  # Command line only
  $0 --hostname miner --miner-ss58 'ss58' --miner-seed 'seed'
  $0 config.yaml --vm-image-dir /custom/vm-images/
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

  if [[ ! -d "./chutes" ]]; then
    echo "Error: chutes package not found in current directory"
    exit 1
  fi

  # Pre-scan for --benchmark so the correct schema is used during validation.
  # CLI_BENCHMARK is not yet applied to BENCHMARK at this point in the script,
  # so we check the raw CLI variable directly.
  CONFIG_SCHEMA_FLAG=""
  [[ "$CLI_BENCHMARK" == "true" ]] && CONFIG_SCHEMA_FLAG="--benchmark"

  set +e
  CONFIG_OUTPUT=$(python3 -m chutes.guest.config $CONFIG_SCHEMA_FLAG "$CONFIG_FILE" 2>&1)
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
[[ -n "$CLI_VM_IMAGE_DIR" ]] && VM_IMAGE_DIR="$CLI_VM_IMAGE_DIR"
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

[[ -n "$CLI_SSH_PORT" ]] && SSH_PORT="$CLI_SSH_PORT"
[[ -n "$CLI_NETWORK_TYPE" ]] && NETWORK_TYPE="$CLI_NETWORK_TYPE"
[[ -n "$CLI_EPHEMERAL" ]] && EPHEMERAL="$CLI_EPHEMERAL"
[[ -n "$CLI_BENCHMARK" ]] && BENCHMARK="$CLI_BENCHMARK"

if [[ -n "$CLI_DOCKER_HUB_USERNAME" || -n "$CLI_DOCKER_HUB_TOKEN" ]]; then
  if [[ -z "$CLI_DOCKER_HUB_USERNAME" || -z "$CLI_DOCKER_HUB_TOKEN" ]]; then
    echo "Error: use both --docker-hub-username and --docker-hub-token together (or neither)."
    exit 1
  fi
  DOCKER_HUB_USERNAME="$CLI_DOCKER_HUB_USERNAME"
  DOCKER_HUB_TOKEN="$CLI_DOCKER_HUB_TOKEN"
fi

# --------------------------------------------------------------------
# Resolve public interface
# Empty = auto-detect from default route (normal case).
# Non-empty = explicit override (multi-homed hosts); warn if the named
# interface doesn't exist so a stale NIC name after an OS upgrade is
# caught here rather than silently producing broken iptables rules.
# --------------------------------------------------------------------
if [[ -z "$PUBLIC_IFACE" ]] || ! ip link show "$PUBLIC_IFACE" >/dev/null 2>&1; then
  DETECTED_IFACE=$(ip -j route show default 2>/dev/null \
    | python3 -c "import json,sys; r=json.load(sys.stdin); print(r[0]['dev'] if r else '')" \
    2>/dev/null || true)
  if [[ -z "$DETECTED_IFACE" ]]; then
    echo "Error: could not determine public interface — auto-detection found no default route."
    echo "  Fix: set network.public_interface in your config.yaml, or pass --public-iface."
    exit 1
  fi
  if [[ -n "$PUBLIC_IFACE" ]]; then
    echo "⚠ Warning: configured public interface '$PUBLIC_IFACE' not found on this system."
    echo "  Auto-detected '$DETECTED_IFACE' from default route. Updating PUBLIC_IFACE."
    echo "  Update network.public_interface in config.yaml to silence this warning."
  fi
  PUBLIC_IFACE="$DETECTED_IFACE"
fi

# --------------------------------------------------------------------
# Deferred --clean: runs after config + CLI overrides so bridge cleanup
# uses the correct PUBLIC_IFACE, BRIDGE_IP, and VM_IP from config.yaml.
# --------------------------------------------------------------------
if [[ "$CLI_CLEAN" == "true" ]]; then
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

  ./network/setup-bridge.sh --clean \
    --bridge-ip "$BRIDGE_IP" \
    --vm-ip "${VM_IP}/24" \
    --public-iface "$PUBLIC_IFACE" 2>/dev/null || true


  if systemctl is-active --quiet benchmark-netlog 2>/dev/null; then
    echo "Stopping benchmark network logging service..."
    sudo systemctl stop benchmark-netlog
    echo "✓ benchmark-netlog stopped"
  fi

  exit 0
fi

# Benchmark mode: set defaults before the general defaults below. The benchmark image is
# a published image set (directory) like every other image — assemble one with
# `chutes.guest.image_set manifest` if you're pointing at a loose qcow2.
if [[ "$BENCHMARK" == "true" ]]; then
  [[ -z "$BASE_IMAGE" ]] && BASE_IMAGE="/var/lib/chutes/base-images/tdx-guest-benchmark"
  # Miner credentials are not used in benchmark mode; set placeholders to satisfy any downstream checks
  [[ -z "$MINER_SS58" ]] && MINER_SS58="benchmark"
  [[ -z "$MINER_SEED" ]] && MINER_SEED="benchmark"
fi

# Default base image: the published image-set directory (qcow2 + boot artifacts +
# manifest) that `--download` populates. There is one image format — the set directory;
# a missing set fails cleanly here rather than being auto-downloaded at launch.
[[ -z "$BASE_IMAGE" ]] && BASE_IMAGE="/var/lib/chutes/base-images/tdx-guest"
if [[ "$EPHEMERAL" == "true" ]]; then
  VM_IMAGE_DIR="/tmp/chutes-vm-images"
elif [[ -z "$VM_IMAGE_DIR" ]]; then
  VM_IMAGE_DIR="/var/lib/chutes/vm-images"
fi

# Validate network type
if [[ "$NETWORK_TYPE" != "tap" && "$NETWORK_TYPE" != "user" ]]; then
  echo "Error: --network-type must be 'tap' or 'user'"
  exit 1
fi

# --------------------------------------------------------------------
# Validate required parameters (must come from YAML or CLI)
# --------------------------------------------------------------------
if [[ "$BENCHMARK" == "true" ]]; then
  if [[ -z "$HOSTNAME" ]]; then
    echo "Error: Missing required configuration:"
    echo "  - hostname (vm.hostname or --hostname)"
    echo ""
    echo "Provide via config file or command line, for example:"
    echo "  $0 --benchmark config.benchmark.yaml"
    exit 1
  fi
else
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
echo "Mode: $([[ "$BENCHMARK" == "true" ]] && echo "benchmark" || echo "standard")"
echo "Hostname: $HOSTNAME"
echo "Base image: $BASE_IMAGE"
echo "VM image dir: $VM_IMAGE_DIR"
echo "VM IP: $VM_IP"
echo "Bridge IP: $BRIDGE_IP"
if [[ "$BENCHMARK" != "true" ]]; then
  echo "Cache volume: $CACHE_VOLUME ($CACHE_SIZE)"
fi
echo "Storage volume: $STORAGE_VOLUME ($STORAGE_SIZE)"
echo "Binding: $([[ "$SKIP_BIND" == "true" ]] && echo "Skipped" || echo "Enabled")"
echo "Network: $NETWORK_TYPE"
echo ""

# --------------------------------------------------------------------
# Refuse duplicate chutes-td QEMU unless --force
# --------------------------------------------------------------------
if [[ "$CLI_FORCE" != "true" ]]; then
  if _live_chutes_td_qemu_running; then
    echo "Error: TDX VM (QEMU, $_PROCESS_NAME_CHUTES_TD) is already running."
    echo "Stop it first: ./quick-launch.sh --clean"
    echo "Or pass --force only if you intend to override this check (not recommended)."
    exit 1
  fi
fi

# --------------------------------------------------------------------
# Step 0: Verify host configuration
# --------------------------------------------------------------------
echo "Step 0: Verifying host configuration..."

# Check TDX is active using sysfs (persists across dmesg ring-buffer rollover)
# then /proc/cpuinfo, and finally dmesg as a last resort.
_tdx_ok=0
_tdx_source=""

# kvm_intel exposes whether TDX support is compiled and active
if [[ "$(cat /sys/module/kvm_intel/parameters/tdx 2>/dev/null)" == "Y" ]]; then
  _tdx_ok=1
  _tdx_source="sysfs (/sys/module/kvm_intel/parameters/tdx=Y)"
fi

# /proc/cpuinfo reports tdx_host_platform when TDX is enabled in the firmware/kernel
if [[ $_tdx_ok -eq 0 ]] && grep -qw 'tdx_host_platform\|tdx' /proc/cpuinfo 2>/dev/null; then
  _tdx_ok=1
  _tdx_source="/proc/cpuinfo"
fi

# Fallback: dmesg ring buffer — may be absent on long-running hosts
if [[ $_tdx_ok -eq 0 ]]; then
  TDX_DMESG=$(sudo dmesg | grep -i tdx 2>/dev/null || echo "")
  if echo "$TDX_DMESG" | grep -q "module initialized"; then
    _tdx_ok=1
    _tdx_source="dmesg"
  fi
fi

if [[ $_tdx_ok -eq 0 ]]; then
  echo "✗ Error: TDX does not appear to be active on this host"
  echo ""
  echo "Checked: sysfs, /proc/cpuinfo, dmesg — none confirmed TDX active."
  TDX_DMESG="${TDX_DMESG:-$(sudo dmesg | grep -i tdx 2>/dev/null || echo "")}"
  if [[ -n "$TDX_DMESG" ]]; then
    echo "TDX-related dmesg entries:"
    echo "$TDX_DMESG" | tail -n 10
  fi
  echo ""
  echo "To enable TDX:"
  echo "  1. Verify CPU supports TDX: grep tdx /proc/cpuinfo"
  echo "  2. Enable TDX in BIOS/UEFI settings"
  echo "  3. Ensure TDX kernel support is installed"
  echo "  4. Reboot and check: cat /sys/module/kvm_intel/parameters/tdx"
  exit 1
fi

echo "✓ TDX active (confirmed via $_tdx_source)"

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

# Device binding to vfio-pci is handled inside run-td (chutes.guest.passthrough)
echo ""


# --------------------------------------------------------------------
# Cache volume (not used in benchmark mode — partner manages storage directly)
# --------------------------------------------------------------------
if [[ "$BENCHMARK" != "true" ]]; then
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
fi

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
# Config volume (hostname + network for all modes; miner creds for production only)
# --------------------------------------------------------------------
echo "Step 4: Setting up config volume..."
if [[ -z "$CONFIG_VOLUME" ]]; then
  CONFIG_VOLUME="config-${HOSTNAME}.qcow2"
fi
if [[ -f "$CONFIG_VOLUME" ]]; then
  echo "Refreshing existing config volume from current config: $CONFIG_VOLUME"
else
  echo "Creating config volume: $CONFIG_VOLUME"
fi
if [[ "$BENCHMARK" == "true" ]]; then
  # Benchmark: config volume carries hostname + network config only.
  # Pass empty miner credentials so create-config.sh skips writing those files.
  if sudo ./volumes/create-config.sh "$CONFIG_VOLUME" "$HOSTNAME" "" "" "$VM_IP" "${BRIDGE_IP%/*}" "$VM_DNS"; then
    echo "✓ Config volume ready (benchmark: no miner credentials)"
  else
    echo "✗ Error: Failed to set up config volume at $CONFIG_VOLUME"
    exit 1
  fi
else
  if run_create_config "$CONFIG_VOLUME"; then
    echo "✓ Config volume ready"
  else
    echo "✗ Error: Failed to set up config volume at $CONFIG_VOLUME"
    exit 1
  fi
fi
echo ""

# --------------------------------------------------------------------
# Step 4b: Instantiate the per-VM copy of the image set (verify against manifest)
# --------------------------------------------------------------------
echo "Step 4b: Preparing VM image (verify set + per-VM copy)..."
VM_IMAGE=$(./prepare-vm-image.sh "$BASE_IMAGE" "$HOSTNAME" "$VM_IMAGE_DIR" | tail -1)
# Pipeline masks exit status; PIPESTATUS[0] is prepare-vm-image's exit code
[[ ${PIPESTATUS[0]} -ne 0 ]] && { echo "Error: VM image preparation failed (see output above)"; exit 1; }
[[ -z "$VM_IMAGE" ]] && { echo "Error: Failed to get VM image path"; exit 1; }
echo ""

# --------------------------------------------------------------------
# Bridge networking
# --------------------------------------------------------------------
NET_IFACE=""
if [[ "$NETWORK_TYPE" == "tap" ]]; then
  echo "Step 5: Setting up bridge networking..."
  BRIDGE_OUTPUT=$(./network/setup-bridge.sh \
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
# Benchmark network logging (install + start if in benchmark mode)
# Installs from repo-relative ./network/ to ensure the running version
# is always in sync with the checked-out scripts.
# --------------------------------------------------------------------
if [[ "$BENCHMARK" == "true" && "$NETWORK_TYPE" == "tap" ]]; then
  echo "Step 5b: Installing and starting benchmark network logging service..."

  NETLOG_SCRIPT_SRC="./network/benchmark-netlog.sh"
  NETLOG_SERVICE_SRC="./network/benchmark-netlog.service"
  NETLOG_LOGRORATE_SRC="./network/benchmark-netlog.logrotate"
  NETLOG_SCRIPT_DST="/usr/local/bin/benchmark-netlog.sh"
  NETLOG_SERVICE_DST="/etc/systemd/system/benchmark-netlog.service"
  NETLOG_LOGROTATE_DST="/etc/logrotate.d/benchmark-netlog"
  NETLOG_ENV_DIR="/etc/chutes"
  NETLOG_ENV_FILE="$NETLOG_ENV_DIR/benchmark-netlog.env"

  for src in "$NETLOG_SCRIPT_SRC" "$NETLOG_SERVICE_SRC" "$NETLOG_LOGRORATE_SRC"; do
    if [[ ! -f "$src" ]]; then
      echo "✗ Error: $src not found. Run from the host-tools/scripts/ directory."
      exit 1
    fi
  done

  sudo install -m 0755 "$NETLOG_SCRIPT_SRC" "$NETLOG_SCRIPT_DST"
  sudo install -m 0644 "$NETLOG_SERVICE_SRC" "$NETLOG_SERVICE_DST"
  sudo install -m 0644 "$NETLOG_LOGRORATE_SRC" "$NETLOG_LOGROTATE_DST"

  # Create env file with bridge subnet if not already present (operator can customise)
  if [[ ! -f "$NETLOG_ENV_FILE" ]]; then
    sudo mkdir -p "$NETLOG_ENV_DIR"
    sudo tee "$NETLOG_ENV_FILE" > /dev/null <<EOF
BRIDGE_SUBNET=${BRIDGE_IP}
NETLOG_DIR=/var/log/chutes/benchmark-netlog
EOF
    echo "  Created $NETLOG_ENV_FILE (customise BRIDGE_SUBNET / NETLOG_DIR as needed)"
  fi

  sudo systemctl daemon-reload
  sudo systemctl enable benchmark-netlog 2>/dev/null || true
  sudo systemctl restart benchmark-netlog
  echo "✓ benchmark-netlog service installed and running"
  echo "  Logs: /var/log/chutes/benchmark-netlog/"
  echo ""
fi

# --------------------------------------------------------------------
# Launch VM
# --------------------------------------------------------------------
echo "Launching Chutes VM..."

LAUNCH_ARGS=(
  --pass-gpus
  --image "$VM_IMAGE"
  --network-type "$NETWORK_TYPE"
)

if [[ "$NETWORK_TYPE" == "tap" ]]; then
  LAUNCH_ARGS+=(--net-iface "$NET_IFACE")
fi

if [[ "$BENCHMARK" == "true" ]]; then
  # Benchmark: no cache volume (partner manages storage directly via luks-setup).
  # Config volume IS created (hostname + network config only, no miner creds).
  LAUNCH_ARGS+=(--ssh)
  LAUNCH_ARGS+=(--config-volume "$CONFIG_VOLUME")
  LAUNCH_ARGS+=(--storage-volume "$STORAGE_VOLUME")
else
  LAUNCH_ARGS+=(--config-volume "$CONFIG_VOLUME")
  LAUNCH_ARGS+=(--cache-volume "$CACHE_VOLUME")
  LAUNCH_ARGS+=(--storage-volume "$STORAGE_VOLUME")
fi
[[ "$FOREGROUND" == "true" ]] && LAUNCH_ARGS+=(--foreground)

# Call Python runner
if ! python3 ./run-td "${LAUNCH_ARGS[@]}"; then
  echo ""
  echo "Error: VM launch failed (run-td exited non-zero). See output above and /tmp/tdx-guest-td.log if daemonized."
  exit 1
fi

echo ""
echo "=== Chutes VM Deployed Successfully ==="
echo ""

exit 0
