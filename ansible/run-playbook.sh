#!/bin/sh
# Automated Ansible playbook execution

echo "Installing Ansible..."
pip install -q ansible 2>&1 | head -20

echo "Waiting for Apache container to be ready..."
sleep 5

echo "Running Ansible playbook..."
ansible-playbook -i inventory.ini site.yml

echo "Ansible playbook execution complete."
echo "Keeping container alive..."
tail -f /dev/null
