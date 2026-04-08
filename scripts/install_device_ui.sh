#!/bin/bash
# ============================================================
# MeetingBox Installation Script
# ============================================================
# Installs everything via Docker. The device-ui, backend,
# transcription, audio, and AI services all run as containers.
#
# Usage:
#   cd /path/to/meetingbox-repo
#   sudo bash scripts/install_device_ui.sh
#
# Tested on Debian/Ubuntu Linux with X11
# ============================================================

set -e

echo "=========================================="
echo "  MeetingBox Full Installation"
echo "=========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

# Get the actual user who called sudo
ACTUAL_USER=${SUDO_USER:-$USER}
ACTUAL_HOME=$(eval echo ~"$ACTUAL_USER")
INSTALL_DIR="/opt/meetingbox"

# Detect where the script is run from (repo root)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$REPO_DIR/docker-compose.yml" ]; then
    echo "ERROR: Cannot find docker-compose.yml"
    echo "Please run this script from the repo root:"
    echo "  cd /path/to/meetingbox && sudo bash scripts/install_device_ui.sh"
    exit 1
fi

echo "Repo:     $REPO_DIR"
echo "Install:  $INSTALL_DIR"
echo "User:     $ACTUAL_USER"
echo ""

# -------------------------------------------------------
# 1. System dependencies (minimal — just X11, display, Docker)
# -------------------------------------------------------
echo "1/7  Installing system dependencies..."
apt-get update
apt-get install -y --no-install-recommends \
    xserver-xorg \
    xinit \
    x11-xserver-utils \
    unclutter \
    curl \
    rsync

# Install Docker if not present
if ! command -v docker &>/dev/null; then
    echo "   Installing Docker..."
    curl -fsSL https://get.docker.com | sh
fi

# Install Docker Compose plugin if not present
if ! docker compose version &>/dev/null; then
    echo "   Installing Docker Compose plugin..."
    apt-get install -y docker-compose-plugin 2>/dev/null || {
        # Fallback: install from Docker's official repo
        mkdir -p /usr/local/lib/docker/cli-plugins
        ARCH=$(dpkg --print-architecture)
        curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH}" \
            -o /usr/local/lib/docker/cli-plugins/docker-compose
        chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    }
fi

# Ensure user is in required groups
usermod -aG docker,video,input,audio "$ACTUAL_USER"
echo "   Done"

# -------------------------------------------------------
# 2. Copy project files to /opt/meetingbox
# -------------------------------------------------------
echo ""
echo "2/7  Setting up project directory..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/data/config"
mkdir -p "$INSTALL_DIR/data/audio/recordings"
mkdir -p "$INSTALL_DIR/data/transcripts"

echo "   Copying project files to $INSTALL_DIR..."
rsync -a \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='venv' \
    --exclude='.venv' \
    "$REPO_DIR/" "$INSTALL_DIR/"

chown -R "$ACTUAL_USER:$ACTUAL_USER" "$INSTALL_DIR"
echo "   Done"

# -------------------------------------------------------
# 3. Configure X11 display
# -------------------------------------------------------
echo ""
echo "3/7  Configuring display..."

# Auto-start X on tty1 login
# NOTE: Disabled during development. Uncomment for production with screen.
BASHRC="$ACTUAL_HOME/.bashrc"
echo "   Skipping auto-startx (dev mode — enable for production)"
# if ! grep -q "startx" "$BASHRC" 2>/dev/null; then
#     cat >> "$BASHRC" << 'XSTART'
#
# # Auto-start X on tty1 (MeetingBox display)
# if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
#     startx -- -nocursor 2>/dev/null
# fi
# XSTART
#     echo "   Added auto-startx to .bashrc"
# else
#     echo "   startx already in .bashrc, skipping"
# fi

# NOTE: .xinitrc and auto-login are disabled during development.
# Use scripts/deploy_production.sh for kiosk-style production setup.
echo "   Skipping .xinitrc creation (dev mode)"

# -------------------------------------------------------
# 4. Auto-login on tty1 (disabled during dev)
# -------------------------------------------------------
echo ""
echo "4/7  Skipping auto-login on tty1 (dev mode — use deploy_production.sh for production)"

# -------------------------------------------------------
# 5. Systemd service for Docker Compose
# -------------------------------------------------------
echo ""
echo "5/7  Creating systemd service..."

cat > /etc/systemd/system/meetingbox.service << EOF
[Unit]
Description=MeetingBox (all services via Docker Compose)
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$INSTALL_DIR
ExecStartPre=/bin/bash -c 'until docker info >/dev/null 2>&1; do sleep 2; done'
ExecStart=/usr/bin/docker compose --profile backend --profile frontend up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

## Onboarding service (hotspot + setup portal for first boot)
cp "$INSTALL_DIR/systemd/meetingbox-onboard.service" /etc/systemd/system/
chmod +x "$INSTALL_DIR/scripts/hotspot.sh"
chmod +x "$INSTALL_DIR/scripts/onboard_server.py"

systemctl daemon-reload
systemctl enable meetingbox.service
systemctl enable meetingbox-onboard.service
echo "   Done"

# -------------------------------------------------------
# 6. Clean up old state, build & start containers
# -------------------------------------------------------
echo ""
echo "6/7  Building and starting Docker containers..."
cd "$INSTALL_DIR"

# Stop anything running from a previous install
docker compose down 2>/dev/null || true

# Build all images
echo "   Building images (this may take a few minutes on first run)..."
docker compose --profile backend --profile frontend build

# Start everything
echo "   Starting containers..."
docker compose --profile backend --profile frontend up -d

echo "   Waiting for services to start..."
sleep 15

# Quick health check
echo ""
if curl -sf http://localhost:8000/health | grep -q "healthy"; then
    echo "   ✓ Backend is healthy"
else
    echo "   ⚠ Backend not responding yet (may still be starting)"
    echo "     Check with: docker compose logs web"
fi

if docker ps --format '{{.Names}}' | grep -q meetingbox-ui; then
    echo "   ✓ Device UI container is running"
else
    echo "   ⚠ Device UI container not running"
    echo "     Check with: docker logs meetingbox-ui"
fi

# -------------------------------------------------------
# 7. mDNS so the device is reachable at meetingbox.local
# -------------------------------------------------------
echo ""
echo "7/7  Enabling mDNS (meetingbox.local)..."
apt-get install -y --no-install-recommends avahi-daemon avahi-utils 2>/dev/null || true
systemctl enable avahi-daemon 2>/dev/null || true
systemctl start avahi-daemon 2>/dev/null || true
echo "   Done"

# -------------------------------------------------------
# Summary
# -------------------------------------------------------
echo ""
echo "=========================================="
echo "  Installation complete!"
echo "=========================================="
echo ""
echo "All services run inside Docker — no Python or pip on the host."
echo ""
echo "  Project dir:     $INSTALL_DIR"
echo "  Web dashboard:   http://meetingbox.local"
echo "  API health:      http://localhost:8000/health"
echo ""
echo "Commands:"
echo "  docker compose ps              # Container status"
echo "  docker compose logs -f         # All logs"
echo "  docker logs meetingbox-ui -f   # Device UI logs"
echo "  docker compose restart         # Restart everything"
echo "  docker compose down            # Stop everything"
echo "  docker compose up -d --build   # Rebuild & restart"
echo ""
echo "Transcription and summaries use OpenAI + Anthropic APIs (configure API keys in .env)."
echo ""
echo "The screen UI will appear after reboot (X11 auto-starts)."
echo "Reboot now?  sudo reboot"
echo ""
