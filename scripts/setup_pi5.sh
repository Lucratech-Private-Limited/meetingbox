#!/usr/bin/env bash
set -euo pipefail

# Skeleton helper for configuring a Raspberry Pi 5 as a MeetingBox host.
# This is adapted from the Software PRD; adjust commands as needed.

echo "Updating system packages..."
sudo apt update && sudo apt upgrade -y

echo "Installing base tools..."
sudo apt install -y git curl wget avahi-daemon avahi-utils net-tools

echo "Installing Docker..."
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker "$USER"

echo "Installing Docker Compose plugin..."
sudo apt install -y docker-compose-plugin

echo "Enabling mDNS (meetingbox.local)..."
sudo systemctl enable avahi-daemon
sudo systemctl start avahi-daemon

echo "Base Pi 5 setup complete. Reboot, then clone this repo into /opt/meetingbox and run docker compose."

