#!/bin/bash
set -e

# Configuration
TDX_REPO="tdx"
UBUNTU_VERSION="25.04"
APP_SCRIPT="${APP_SCRIPT:-scripts/setup-app.sh}"
ANSIBLE_DIR="${ANSIBLE_DIR:-ansible/k3s}"
LOGFILE="tdx-image-build.log"
CREATE_TD_SCRIPT="$TDX_REPO/guest-tools/image/create-td-image.sh"
GUEST_IMG_PATH="$TDX_REPO/guest-tools/image/tdx-guest-ubuntu-$UBUNTU_VERSION-generic.qcow2"

# Ensure prerequisites
echo "Installing dependencies..." | tee -a $LOGFILE
sudo apt update >> $LOGFILE 2>&1
sudo apt install -y git libguestfs-tools virtinst genisoimage libvirt-daemon-system >> $LOGFILE 2>&1

# Fix libvirt default network
echo "Ensuring libvirt default network is active..." | tee -a $LOGFILE
sudo virsh net-start default >> $LOGFILE 2>&1 || true
sudo virsh net-autostart default >> $LOGFILE 2>&1 || true

# Clone or update canonical/tdx
if [ ! -d "$TDX_REPO" ]; then
    echo "Initializing canonical/tdx submodule..." | tee -a $LOGFILE
    git submodule update --init --recursive >> $LOGFILE 2>&1
fi
cd $TDX_REPO
echo "Checking out main branch for canonical/tdx..." | tee -a ../$LOGFILE
git checkout main >> ../$LOGFILE 2>&1
cd ..

# Ensure APP_SCRIPT and ANSIBLE_DIR exist
if [ ! -f "$APP_SCRIPT" ]; then
    echo "Error: $APP_SCRIPT not found. Please provide a valid setup script." | tee -a $LOGFILE
    exit 1
fi
if [ ! -d "$ANSIBLE_DIR" ]; then
    echo "Error: $ANSIBLE_DIR not found. Please provide a valid Ansible directory." | tee -a $LOGFILE
    exit 1
fi

# Run create-td-image.sh to generate base image
echo "Building base TDX guest image..." | tee -a $LOGFILE
cd $TDX_REPO/guest-tools/image
sudo ./create-td-image.sh -v $UBUNTU_VERSION >> ../../$LOGFILE 2>&1
cd ../../..
if [ ! -f "$GUEST_IMG_PATH" ]; then
    echo "Error: Failed to create base TDX guest image at $GUEST_IMG_PATH" | tee -a $LOGFILE
    exit 1
fi

# Apply custom setup steps
echo "Applying custom setup steps to TDX guest image..." | tee -a $LOGFILE
sudo virt-customize -a $GUEST_IMG_PATH \
    --mkdir /tmp/app \
    --mkdir /tmp/app/ansible \
    --copy-in $APP_SCRIPT:/tmp/app/ \
    --copy-in $ANSIBLE_DIR:/tmp/app/ansible/ \
    --run-command "/tmp/app/setup-app.sh" >> $LOGFILE 2>&1
if [ $? = 0 ]; then
    echo "Successfully applied custom setup steps to TDX guest image" | tee -a $LOGFILE
else
    echo "Error: Failed to apply custom setup steps to TDX guest image" | tee -a $LOGFILE
    exit 1
fi

# Fix permissions for libvirt-qemu
echo "Fixing permissions for $GUEST_IMG_PATH..." | tee -a $LOGFILE
sudo chown root:libvirt-qemu "$GUEST_IMG_PATH" >> $LOGFILE 2>&1
sudo chmod 640 "$GUEST_IMG_PATH" >> $LOGFILE 2>&1
# Ensure all parent directories from $HOME to image directory have execute permissions
ABS_GUEST_IMG_PATH=$(realpath "$GUEST_IMG_PATH")
PARENT_DIR=$(dirname "$ABS_GUEST_IMG_PATH")
HOME_DIR=$(realpath "$HOME")
while [ "$PARENT_DIR" != "/" ] && [ "$PARENT_DIR" != "$HOME_DIR" ]; do
    echo "Setting execute permissions on $PARENT_DIR..." | tee -a $LOGFILE
    sudo chmod o+x "$PARENT_DIR" >> $LOGFILE 2>&1
    PARENT_DIR=$(dirname "$PARENT_DIR")
done
echo "Setting execute permissions on $HOME_DIR..." | tee -a $LOGFILE
sudo chmod o+x "$HOME_DIR" >> $LOGFILE 2>&1

# Output result
echo "TDX guest image created: $GUEST_IMG_PATH" | tee -a $LOGFILE
echo "Run './scripts/test-vm.sh' to test the image locally." | tee -a $LOGFILE