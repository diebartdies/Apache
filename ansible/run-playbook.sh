#!/bin/sh
set -eu

echo "Installing Ansible..."
pip install -q ansible

if ! command -v docker >/dev/null 2>&1; then
	echo "Installing Docker CLI..."
	apt-get update >/dev/null 2>&1
	DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker.io >/dev/null 2>&1
fi

echo "Waiting for Apache container to be ready..."
sleep 5

echo "Running Ansible playbook..."
ansible-playbook -i /ansible/inventory.ini /ansible/site.yml

echo "Ansible playbook execution complete."
echo "Keeping container alive..."
tail -f /dev/null
