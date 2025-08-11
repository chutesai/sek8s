#!/bin/bash
set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
UBUNTU_VERSION="25.04"
IMAGE_PATH="$REPO_ROOT/guest-tools/image/tdx-guest-ubuntu-$UBUNTU_VERSION-final.qcow2"
VM_NAME="tdx-test-vm"
LOGFILE="tdx-test-vm.log"
VNC_PORT="5900"
USER_DATA_TMPL="${USER_DATA_TMPL:-$REPO_ROOT/local/user-data.tmpl.yaml}"
USER_DATA_FILE="${USER_DATA_FILE:-$REPO_ROOT/local/user-data.yaml}"

# Log function
log() {
    echo "$1" | tee -a $LOGFILE
}

echo "" > $LOGFILE

# Check prerequisites
log "Checking prerequisites..."
if ! command -v qemu-kvm >/dev/null 2>&1; then
    log "Installing dependencies..."
    sudo apt update >> $LOGFILE 2>&1
    sudo apt install -y qemu-kvm libvirt-daemon-system libvirt-clients virtinst virt-manager >> $LOGFILE 2>&1
fi

# Install VNC client (tigervnc-viewer)
if ! command -v vncviewer >/dev/null 2>&1; then
    log "Installing tigervnc-viewer..."
    sudo apt install -y tigervnc-viewer >> $LOGFILE 2>&1 || {
        log "Warning: Failed to install tigervnc-viewer. You can still use 'virsh console $VM_NAME' for text-based access."
    }
fi

# Check KVM support
if ! kvm-ok >/dev/null 2>&1; then
    log "Warning: KVM acceleration not available. Falling back to TCG (slower)."
    VIRT_TYPE="qemu"
else
    VIRT_TYPE="kvm"
fi

# Ensure user is in libvirt group
if ! groups | grep -q libvirt; then
    log "Adding user to libvirt group..."
    sudo usermod -aG libvirt $(whoami) >> $LOGFILE 2>&1
    log "Please run 'newgrp libvirt' or log out and back in to apply group changes."
    exit 1
fi

# Start libvirtd
log "Starting libvirtd..."
sudo systemctl enable --now libvirtd >> $LOGFILE 2>&1
sudo systemctl start libvirtd >> $LOGFILE 2>&1

# Fix default network
log "Ensuring libvirt default network is active..."
sudo virsh net-start default >> $LOGFILE 2>&1 || true
sudo virsh net-autostart default >> $LOGFILE 2>&1 || true

# Check for guest image
if [ ! -f "$IMAGE_PATH" ]; then
    log "Error: Guest image $IMAGE_PATH not found. Run './scripts/build-server-image.sh' with UBUNTU_VERSION=$UBUNTU_VERSION."
    exit 1
fi

# Check for user-data file
if [ -n "$USER_DATA_TMPL" ] && [ ! -f "$USER_DATA_TMPL" ]; then
    log "Error: User-data file $USER_DATA_TMPL not found."
    exit 1
fi

# Fix permissions for libvirt-qemu
log "Fixing permissions for $IMAGE_PATH..."
sudo chown root:libvirt-qemu "$IMAGE_PATH" >> $LOGFILE 2>&1
sudo chmod 640 "$IMAGE_PATH" >> $LOGFILE 2>&1
# Ensure all parent directories from $HOME to image directory have execute permissions
ABS_IMAGE_PATH=$(realpath "$IMAGE_PATH")
PARENT_DIR=$(dirname "$ABS_IMAGE_PATH")
HOME_DIR=$(realpath "$HOME")
while [ "$PARENT_DIR" != "/" ] && [ "$PARENT_DIR" != "$HOME_DIR" ]; do
    log "Setting execute permissions on $PARENT_DIR..."
    sudo chmod o+x "$PARENT_DIR" >> $LOGFILE 2>&1
    PARENT_DIR=$(dirname "$PARENT_DIR")
done
# Ensure $HOME has execute permissions
log "Setting execute permissions on $HOME_DIR..."
sudo chmod o+x "$HOME_DIR" >> $LOGFILE 2>&1

# Verify permissions
log "Verifying permissions for $IMAGE_PATH..."
if ! sudo -u libvirt-qemu test -r "$IMAGE_PATH"; then
    log "Error: libvirt-qemu cannot read $IMAGE_PATH. Check permissions."
    exit 1
fi
if ! sudo -u libvirt-qemu test -x "$(dirname "$IMAGE_PATH")"; then
    log "Error: libvirt-qemu cannot traverse directory $(dirname "$IMAGE_PATH"). Check execute permissions."
    exit 1
fi

# Check if VM already exists
if virsh list --all | grep -q "$VM_NAME"; then
    log "Warning: VM $VM_NAME already exists. Destroying and undefining..."
    virsh destroy $VM_NAME >> $LOGFILE 2>&1 || true
    virsh undefine $VM_NAME >> $LOGFILE 2>&1 || true
fi

# Prepare cloud-init user-data
CLOUD_INIT_OPT=""
if [ -f "$USER_DATA_TMPL" ]; then

    # Example substitution before using the user-data
    sed -e "s/MINER_SS58_PLACEHOLDER/$MINER_SS58/" \
        -e "s/MINER_SEED_PLACEHOLDER/$MINER_SEED/" \
        $USER_DATA_TMPL > $USER_DATA_FILE

    log "Using user-data file: $USER_DATA_TMPL"
    CLOUD_INIT_OPT="--cloud-init user-data=$USER_DATA_FILE"
fi

# Copy files into image before starting VM
sudo virt-customize -a "$IMAGE_PATH" \
    --copy-in $REPO_ROOT/guest-tools/tests:/root \
    --run-command 'find /root/tests -type f -name "*.sh" -exec chmod 755 {} \;'

# Start the VM
log "Starting VM $VM_NAME..."
virt-install \
    --name $VM_NAME \
    --ram 3072 \
    --vcpus 2 \
    --disk path=$IMAGE_PATH,format=qcow2 \
    --os-variant ubuntu$UBUNTU_VERSION \
    --virt-type $VIRT_TYPE \
    --network network=default \
    --graphics vnc,listen=0.0.0.0,port=$VNC_PORT \
    --import \
    $CLOUD_INIT_OPT \
    --noautoconsole >> $LOGFILE 2>&1
if [ $? -eq 0 ]; then
    log "VM $VM_NAME started successfully."
else
    log "Error: Failed to start VM. Check $LOGFILE for details."
    exit 1
fi

# Provide connection instructions
log "Connect to the VM using one of the following methods:"
log "2. Console: Run 'virsh console $VM_NAME' to access the text console (exit with Ctrl+])."
log "Default credentials (if not customized):"
log "  Username: tdx"
log "  Password: 123456"
log "To customize credentials, edit cloud-init in setup-server.sh or check tdx/guest-tools/image/cloud-init/."

# Validate custom setup
if [ -f "$USER_DATA_TMPL" ]; then
    log "Validating custom setup (connect to VM to run these checks):"
    log "1. Check user-data: 'cat /var/lib/cloud/instance/user-data.txt'"
    log "2. Check hostname: 'hostname' and 'cat /etc/hostname'"
fi

# Instructions for cleanup
log "To stop and remove the VM after testing:"
log "  virsh destroy $VM_NAME"
log "  virsh undefine $VM_NAME"
log "The guest image ($IMAGE_PATH) remains for cloud deployment."