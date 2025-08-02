#!/bin/bash
set -e

# Configuration
UBUNTU_VERSION="25.04"
IMAGE_PATH="tdx/guest-tools/image/tdx-guest-ubuntu-$UBUNTU_VERSION-generic.qcow2"
VM_NAME="tdx-test-vm"
LOGFILE="tdx-test-vm.log"
VNC_PORT="5900"

# Log function
log() {
    echo "$1" | tee -a $LOGFILE
}

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
    log "Error: Guest image $IMAGE_PATH not found. Run './scripts/build-td-image.sh' with UBUNTU_VERSION=$UBUNTU_VERSION."
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
    --noautoconsole >> $LOGFILE 2>&1
if [ $? -eq 0 ]; then
    log "VM $VM_NAME started successfully."
else
    log "Error: Failed to start VM. Check $LOGFILE for details."
    exit 1
fi

# Provide connection instructions
log "Connect to the VM using one of the following methods:"
log "1. VNC: Run 'vncviewer localhost:$VNC_PORT' to access the graphical console."
log "2. Console: Run 'virsh console $VM_NAME' to access the text console (exit with Ctrl+])."
log "Default credentials (if not customized):"
log "  Username: tdx"
log "  Password: 123456"
log "To customize credentials, edit cloud-init in setup-app.sh or check tdx/guest-tools/image/cloud-init/."

# Validate custom setup
log "Validating custom setup (connect to VM to run these checks):"
log "1. Check installed packages: 'dpkg -l | grep -E \"ansible|docker|python3\"'"
log "2. Verify Ansible playbooks: 'ls /root/ansible/'"
log "3. Check k3s (if installed): 'sudo systemctl status k3s' and 'kubectl get nodes'"
log "4. Check environment variables: 'cat /root/.bashrc | grep APP_CONFIG'"
log "5. Check Bittensor (if installed): 'which btcli' or 'sudo systemctl status bittensor'"

# Instructions for cleanup
log "To stop and remove the VM after testing:"
log "  virsh destroy $VM_NAME"
log "  virsh undefine $VM_NAME"
log "The guest image ($IMAGE_PATH) remains for cloud deployment."