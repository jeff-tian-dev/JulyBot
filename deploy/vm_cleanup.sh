#!/usr/bin/env bash
# Remove previous JulyBot deploy artifacts from the VM.
set -euo pipefail

echo "==> Stopping service..."
sudo systemctl stop julybot-twitter 2>/dev/null || true
sudo systemctl disable julybot-twitter 2>/dev/null || true
sudo rm -f /etc/systemd/system/julybot-twitter.service
sudo systemctl daemon-reload

echo "==> Removing app directory..."
sudo rm -rf /opt/julybot

echo "==> Removing extra swap from prior setup..."
sudo swapoff /swapfile 2>/dev/null || true
sudo rm -f /swapfile
sudo sed -i '/^\/swapfile /d' /etc/fstab 2>/dev/null || true

echo "Cleanup complete."
