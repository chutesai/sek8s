#!/bin/bash
set -e

echo "Installing dependencies for application setup..."
apt-get update
apt-get install -y ansible python3 python3-pip curl docker.io

echo "Configuring UTF-8 locale..."
locale-gen en_US.UTF-8
export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8
export LANGUAGE=en_US.UTF-8
update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

echo "Copying Ansible playbooks..."
mkdir -p /root/ansible
cp -r /tmp/app/ansible/* /root/ansible/

echo "Running Ansible playbook..."
ansible-playbook /root/ansible/main.yml

echo "Configuring application environment..."
# Add subnet-specific configuration here
echo "export APP_CONFIG=/root/app/config" >> /root/.bashrc

echo "Cleaning up..."
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/app

echo "Application setup completed."