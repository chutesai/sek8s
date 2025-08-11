#!/bin/bash
set -e

# Resolve the absolute path of the script's directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Configuration
TDX_REPO="$REPO_ROOT/tdx"
UBUNTU_VERSION="25.04"
BOOT_SCRIPT="${BOOT_SCRIPT:-$REPO_ROOT/guest-tools/scripts/setup-server.sh}"
ANSIBLE_DIR="${ANSIBLE_DIR:-$REPO_ROOT/ansible}"
CHARTS_DIR="${CHARTS_DIR:-$REPO_ROOT/charts}"
BOOT_SCRIPTS_DIR="${BOOT_SCRIPTS_DIR:-$REPO_ROOT/guest-tools/scripts/boot}"
LOGFILE="$REPO_ROOT/tdx-image-build.log"
GUEST_IMG_PATH="$TDX_REPO/guest-tools/image/tdx-guest-ubuntu-$UBUNTU_VERSION-generic.qcow2"
IMG_DIR="$REPO_ROOT/guest-tools/image"
SEK8S_IMG_PATH="$IMG_DIR/tdx-guest-ubuntu-$UBUNTU_VERSION.qcow2"
FINAL_IMG_PATH="$IMG_DIR/tdx-guest-ubuntu-$UBUNTU_VERSION-final.qcow2"
VM_NAME="tdx-build"
VNC_PORT="5901"
CLOUD_INIT_DIR="$REPO_ROOT/local/cloud-init"
USER_DATA_FILE="$CLOUD_INIT_DIR/user-data"
NO_CACHE="${NO_CACHE:-false}"
DEBUG="${DEBUG:-false}"

# Log function
log() {
    echo "$1" | tee -a "$LOGFILE"
}

DEBUG=$(echo "$DEBUG" | tr '[:upper:]' '[:lower:]')

echo "" > "$LOGFILE"

# Ensure prerequisites
log "Installing dependencies..."
sudo apt update >> "$LOGFILE" 2>&1
sudo apt install -y git libguestfs-tools virtinst genisoimage libvirt-daemon-system >> "$LOGFILE" 2>&1

# Fix libvirt default network
log "Ensuring libvirt default network is active..."
sudo virsh net-start default >> "$LOGFILE" 2>&1 || true
sudo virsh net-autostart default >> "$LOGFILE" 2>&1 || true

# Ensure base image exists
if [ ! -f "$GUEST_IMG_PATH" ]; then
    log "Base image does not exist. Run build-base-image.sh."
    exit 1
fi

# Handle NO_CACHE
NO_CACHE=$(echo "$NO_CACHE" | tr '[:upper:]' '[:lower:]')
if [ "$NO_CACHE" = "true" ]; then
    log "NO_CACHE=true, forcing fresh copy of base image..."
    rm -f "$SEK8S_IMG_PATH" >> "$LOGFILE" 2>&1
fi
if [ ! -f "$SEK8S_IMG_PATH" ]; then
    mkdir -p "$IMG_DIR"
    sudo cp "$GUEST_IMG_PATH" "$SEK8S_IMG_PATH"
fi

# Ensure scripts and directories exist
if [ ! -f "$BOOT_SCRIPT" ]; then
    log "Error: $BOOT_SCRIPT not found."
    exit 1
fi
if [ ! -d "$ANSIBLE_DIR" ]; then
    log "Error: $ANSIBLE_DIR not found."
    exit 1
fi
if [ ! -d "$BOOT_SCRIPTS_DIR" ]; then
    log "Error: $BOOT_SCRIPTS_DIR not found."
    exit 1
fi

# Create user-data for temporary VM
log "Creating temporary user-data..."
mkdir -p "$(dirname "$USER_DATA_FILE")"
cat > "$USER_DATA_FILE" << 'EOF'
#cloud-config
hostname: build-node
timezone: UTC
runcmd:
  - /root/setup-server.sh > /var/log/setup-server.log 2>&1 && echo "SUCCESS" > /root/setup-server-done || echo "ERROR \$?" > /root/setup-server-done
EOF

# Apply custom setup steps
log "Applying custom setup steps to TDX guest image..."
sudo virsh destroy "$VM_NAME" >/dev/null 2>&1 || true
sudo virsh undefine "$VM_NAME" >/dev/null 2>&1 || true
sudo virt-customize -a "$SEK8S_IMG_PATH" \
    --install ansible,python3,python3-pip,curl,docker.io \
    --mkdir /root/ansible \
    --mkdir /root/scripts/boot \
    --copy-in "$BOOT_SCRIPT:/root/" \
    --copy-in "$ANSIBLE_DIR:/root/" \
    --copy-in "$CHARTS_DIR:/root/" \
    --copy-in "$BOOT_SCRIPTS_DIR:/root/scripts" \
    --chmod 755:/root/setup-server.sh \
    --run-command 'find /root/scripts/boot -type f -name "*.sh" -exec chmod 755 {} \;'
if [ $? -eq 0 ]; then
    log "Successfully applied custom setup steps to TDX guest image"
else
    log "Error: Failed to apply custom setup steps to TDX guest image"
    exit 1
fi

# Fix permissions for libvirt-qemu
log "Fixing permissions for $SEK8S_IMG_PATH..."
sudo chown root:libvirt-qemu "$SEK8S_IMG_PATH" >> "$LOGFILE" 2>&1
sudo chmod 640 "$SEK8S_IMG_PATH" >> "$LOGFILE" 2>&1
ABS_SEK8S_IMG_PATH=$(realpath "$SEK8S_IMG_PATH")
PARENT_DIR=$(dirname "$ABS_SEK8S_IMG_PATH")
HOME_DIR=$(realpath "$HOME")
while [ "$PARENT_DIR" != "/" ] && [ "$PARENT_DIR" != "$HOME_DIR" ]; do
    log "Setting execute permissions on $PARENT_DIR..."
    sudo chmod o+x "$PARENT_DIR" >> "$LOGFILE" 2>&1
    PARENT_DIR=$(dirname "$PARENT_DIR")
done
log "Setting execute permissions on $HOME_DIR..."
sudo chmod o+x "$HOME_DIR" >> "$LOGFILE" 2>&1

# Start temporary VM
log "Starting temporary VM $VM_NAME..."
VIRT_TYPE="kvm"
if ! kvm-ok >/dev/null 2>&1; then
    log "Warning: KVM not available, using TCG"
    VIRT_TYPE="qemu"
fi

virt-install \
    --name "$VM_NAME" \
    --ram 3072 \
    --vcpus 2 \
    --disk path="$SEK8S_IMG_PATH",format=qcow2 \
    --os-variant ubuntu$UBUNTU_VERSION \
    --virt-type "$VIRT_TYPE" \
    --network network=default \
    --graphics vnc,listen=0.0.0.0,port="$VNC_PORT" \
    --import \
    --cloud-init user-data="$USER_DATA_FILE" \
    --noautoconsole >> "$LOGFILE" 2>&1
if [ $? -ne 0 ]; then
    log "Error: Failed to start temporary VM"
    exit 1
fi

# Wait for VM to be running
log "Waiting for VM $VM_NAME to be in running state..."
for i in {1..60}; do
    if virsh list --state-running | grep -q "$VM_NAME"; then
        log "VM $VM_NAME is running"
        break
    fi
    log "Waiting for VM to start ($i/60)..."
    sleep 10
done
if ! virsh list --state-running | grep -q "$VM_NAME"; then
    log "Error: VM $VM_NAME not running after 10 minutes"
    virsh destroy "$VM_NAME" >/dev/null 2>&1 || true
    virsh undefine "$VM_NAME" >/dev/null 2>&1 || true
    exit 1
fi

# Check setup-server.sh completion
log "Checking setup-server.sh completion..."
for i in {1..120}; do
    if sudo virt-cat -a "$SEK8S_IMG_PATH" /root/setup-server-done > /tmp/setup-server-status 2>/dev/null; then
        if grep -q "SUCCESS" /tmp/setup-server-status; then
            log "Ansible playbook completed successfully"
            sudo virt-cat -a "$SEK8S_IMG_PATH" /var/log/setup-server.log >> "$LOGFILE" 2>&1
            break
        elif grep -q "ERROR" /tmp/setup-server-status; then
            log "Error: setup-server.sh failed"
            sudo virt-cat -a "$SEK8S_IMG_PATH" /var/log/setup-server.log >> "$LOGFILE" 2>&1
            if [ "$DEBUG" = "true" ]; then
                # Pause for debugging
                log "Paused for debugging before removing build VM. Press Enter to continue and shutdown the VM..."
                read -r
            fi
            virsh destroy "$VM_NAME" >/dev/null 2>&1 || true
            virsh undefine "$VM_NAME" >/dev/null 2>&1 || true
            exit 1
        fi
    fi
    log "Waiting for setup-server.sh ($i/120)..."
    sleep 30
done
if ! sudo virt-cat -a "$SEK8S_IMG_PATH" /root/setup-server-done > /tmp/setup-server-status 2>/dev/null || ! grep -q "SUCCESS" /tmp/setup-server-status; then
    log "Error: setup-server.sh not run or not completed"
    sudo virt-cat -a "$SEK8S_IMG_PATH" /var/log/setup-server.log >> "$LOGFILE" 2>&1 || true
    virsh destroy "$VM_NAME" >/dev/null 2>&1 || true
    virsh undefine "$VM_NAME" >/dev/null 2>&1 || true
    exit 1
fi
rm -f /tmp/setup-server-status

# Allow pause to debug locally if desired
if [ "$DEBUG" = "true" ]; then
    log "Press Enter to continue and shut down the VM..."
    read -r
fi

# Shut down VM
log "Shutting down VM..."
virsh shutdown "$VM_NAME" >> "$LOGFILE" 2>&1
for i in {1..60}; do
    if ! virsh list --state-running | grep -q "$VM_NAME"; then
        log "VM shut down successfully"
        break
    fi
    log "Waiting for VM shutdown ($i/60)..."
    sleep 2
done
if virsh list --state-running | grep -q "$VM_NAME"; then
    log "Error: Failed to shut down VM"
    virsh destroy "$VM_NAME" >/dev/null 2>&1 || true
    exit 1
fi
virsh undefine "$VM_NAME" >> "$LOGFILE" 2>&1

# Clean up cloud-init
rm -rf "$CLOUD_INIT_DIR"

# Clean cloud-init state from the offline image
log "Cleaning cloud-init state for fresh deployments..."
sudo virt-customize -a "$SEK8S_IMG_PATH" \
    --delete /var/lib/cloud/instances \
    --delete /var/lib/cloud/data \
    --delete /var/lib/cloud/instance \
    --delete /var/log/cloud-init.log \
    --delete /var/log/cloud-init-output.log \
    --run-command 'find /var/lib/cloud -name "*.pkl" -delete 2>/dev/null || true' \
    --run-command 'mkdir -p /var/lib/cloud/instances /var/lib/cloud/data'

if [ $? -eq 0 ]; then
    log "Successfully cleaned cloud-init state"
else
    log "Error: Failed to clean cloud-init state"
    exit 1
fi

# Copy to final image
log "Copying $SEK8S_IMG_PATH to $FINAL_IMG_PATH..."
sudo cp "$SEK8S_IMG_PATH" "$FINAL_IMG_PATH"
if [ $? -eq 0 ]; then
    log "Final image created at $FINAL_IMG_PATH"
else
    log "Error: Failed to copy image"
    exit 1
fi

# Verify final image
log "Verifying final image..."
sudo qemu-img check "$FINAL_IMG_PATH" >> "$LOGFILE" 2>&1
if [ $? -eq 0 ]; then
    log "Final image verification passed"
else
    log "Error: Final image verification failed"
    exit 1
fi

# Output result
log "TDX guest image created: $FINAL_IMG_PATH"
log "Run '$REPO_ROOT/guest-tools/scripts/run-image.sh' to test the image locally."