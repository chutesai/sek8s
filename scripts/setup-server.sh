#!/bin/bash
set -e

echo "Installing dependencies for application setup..."
apt-get update
apt-get install -y ansible python3 python3-pip curl docker.io
ansible-galaxy collection install kubernetes.core

echo "Configuring UTF-8 locale..."
locale-gen en_US.UTF-8
export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8
export LANGUAGE=en_US.UTF-8
update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

echo "Configuring first-boot scripts..."
chmod +x /root/scripts/boot/*.sh

echo "Setting up cloud-init to run first-boot scripts..."
cat > /etc/cloud/cloud.cfg.d/99-first-boot.cfg << 'EOF'
#cloud-config
runcmd:
  - for script in /root/scripts/boot/*.sh; do [ -f "$script" ] && bash "$script"; done
EOF

echo "Running Ansible playbook for k3s and system setup..."
cwd=$(pwd)
cd /root/ansible/k3s
ansible-playbook playbooks/site.yml
cd "$cwd"

# Make getty wait for cloud-final
sudo mkdir -p /etc/systemd/system/getty@.service.d/
sudo tee /etc/systemd/system/getty@.service.d/wait-for-cloud-init.conf > /dev/null << 'EOF'
[Unit]
After=cloud-final.service
Wants=cloud-final.service
EOF

# Make SSH wait for cloud-final
sudo mkdir -p /etc/systemd/system/ssh.service.d/
sudo tee /etc/systemd/system/ssh.service.d/wait-for-cloud-init.conf > /dev/null << 'EOF'
[Unit]
After=cloud-final.service
Wants=cloud-final.service
EOF

# Make user sessions wait for cloud-final
sudo mkdir -p /etc/systemd/system/systemd-user-sessions.service.d/
sudo tee /etc/systemd/system/systemd-user-sessions.service.d/wait-for-cloud-init.conf > /dev/null << 'EOF'
[Unit]
After=cloud-final.service
Wants=cloud-final.service
EOF

sudo systemctl daemon-reload

echo "Cleaning up..."
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/app

echo "Application setup completed."