#!/usr/bin/env bash
set -euo pipefail

# -------------------------------------------------------------------
# MeetingBox -- VirtualBox / Linux VM Setup Script
#
# Run this on a fresh Ubuntu Server 24.04 VM to prepare it for
# hosting the MeetingBox Docker stack with real USB mic access.
#
# Usage:
#   chmod +x scripts/setup_vm.sh
#   sudo ./scripts/setup_vm.sh
#
# After this script completes, reboot the VM, then:
#   cd /opt/meetingbox
#   cp .env.example .env   # edit with real ANTHROPIC_API_KEY
#   cd frontend && npm install && npm run build && cd ..
#   docker compose up --build -d
# -------------------------------------------------------------------

REPO_DIR="${1:-/opt/meetingbox}"

echo "============================================"
echo "  MeetingBox VM Setup"
echo "============================================"

# --- System update ---
echo ""
echo "[1/7] Updating system packages..."
sudo apt update && sudo apt upgrade -y

# --- Base tools ---
echo ""
echo "[2/7] Installing base tools..."
sudo apt install -y \
    git curl wget \
    avahi-daemon avahi-utils \
    net-tools \
    alsa-utils \
    build-essential

# --- Docker ---
echo ""
echo "[3/7] Installing Docker..."
if command -v docker &>/dev/null; then
    echo "  Docker already installed: $(docker --version)"
else
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sudo sh /tmp/get-docker.sh
    rm /tmp/get-docker.sh
fi
sudo usermod -aG docker "$USER"

echo ""
echo "[4/7] Installing Docker Compose plugin..."
sudo apt install -y docker-compose-plugin
echo "  Docker Compose: $(docker compose version)"

# --- Node.js (for frontend build) ---
echo ""
echo "[5/7] Installing Node.js 20.x..."
if command -v node &>/dev/null; then
    echo "  Node.js already installed: $(node --version)"
else
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt install -y nodejs
fi
echo "  Node: $(node --version), npm: $(npm --version)"

# --- mDNS / hostname ---
echo ""
echo "[6/7] Configuring hostname and mDNS (meetingbox.local)..."
sudo hostnamectl set-hostname meetingbox
sudo systemctl enable avahi-daemon
sudo systemctl start avahi-daemon

# --- Host-Only network hint ---
echo ""
echo "[7/7] Network configuration hint..."
NETPLAN_FILE="/etc/netplan/01-host-only.yaml"
if [ ! -f "$NETPLAN_FILE" ]; then
    echo "  Creating $NETPLAN_FILE for host-only adapter (enp0s8)..."
    sudo tee "$NETPLAN_FILE" > /dev/null <<'NETPLAN'
network:
  version: 2
  ethernets:
    enp0s8:
      dhcp4: true
NETPLAN
    echo "  Run 'sudo netplan apply' after reboot to activate."
else
    echo "  $NETPLAN_FILE already exists, skipping."
fi

# --- Audio device check ---
echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Reboot the VM:  sudo reboot"
echo "  2. After reboot, verify Docker:  docker --version && docker compose version"
echo "  3. Verify USB mic (after passthrough):  arecord -l"
echo "  4. Clone/copy the repo to $REPO_DIR"
echo "  5. Configure environment:"
echo "       cd $REPO_DIR"
echo "       cp .env.example .env"
echo "       nano .env   # set real ANTHROPIC_API_KEY"
echo "  6. Build frontend:"
echo "       cd $REPO_DIR/frontend && npm install && npm run build && cd .."
echo "  7. Start the stack:"
echo "       docker compose up --build -d"
echo "  8. Verify all containers:"
echo "       docker compose ps"
echo "  9. Open from Windows browser:"
echo "       http://\$(ip -4 addr show enp0s8 | grep -oP '(?<=inet\s)\d+(\.\d+){3}'):8000"
echo ""
echo "NOTE: You must log out and back in (or reboot) for the docker group to take effect."
echo ""
