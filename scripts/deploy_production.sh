#!/bin/bash
# ============================================================
# MeetingBox Production Deployment
# ============================================================
# Prepares a Linux host for autonomous, kiosk-style operation.
# After running this script and rebooting:
#   - No desktop or console is visible
#   - All Docker services (including device-ui) start automatically
#   - The attached display shows the MeetingBox UI immediately
#   - No SSH or manual commands required
#
# Prerequisites:
#   - Debian/Ubuntu Linux with X11 support
#   - A 'meetingbox' user exists (or $SUDO_USER will be used)
#   - Internet connection for package and Docker image downloads
#
# Usage:
#   cd /path/to/meetingbox-repo
#   sudo bash scripts/deploy_production.sh
#
# ============================================================

set -e

echo "============================================"
echo "  MeetingBox — Production Deployment"
echo "============================================"
echo ""

# ----------------------------------------------------------
# Sanity checks
# ----------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run as root (use sudo)"
    exit 1
fi

ACTUAL_USER=${SUDO_USER:-meetingbox}
ACTUAL_HOME=$(eval echo ~"$ACTUAL_USER")
INSTALL_DIR="/opt/meetingbox"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$REPO_DIR/docker-compose.yml" ]; then
    echo "ERROR: Cannot find docker-compose.yml in $REPO_DIR"
    echo "Run from the repo root: sudo bash scripts/deploy_production.sh"
    exit 1
fi

if [ ! -f "$REPO_DIR/docker-compose.prod.yml" ]; then
    echo "ERROR: Missing docker-compose.prod.yml (production overrides)"
    exit 1
fi

echo "Repo:     $REPO_DIR"
echo "Install:  $INSTALL_DIR"
echo "User:     $ACTUAL_USER"
echo "Home:     $ACTUAL_HOME"
echo ""

# ==========================================================
# 1/9  System dependencies
# ==========================================================
echo "1/9  Installing system dependencies..."

apt-get update -qq
apt-get install -y --no-install-recommends \
    xserver-xorg \
    xinit \
    x11-xserver-utils \
    unclutter \
    plymouth \
    plymouth-themes \
    curl \
    rsync \
    avahi-daemon \
    avahi-utils

# Docker
if ! command -v docker &>/dev/null; then
    echo "   Installing Docker..."
    curl -fsSL https://get.docker.com | sh
fi

# Docker Compose plugin
if ! docker compose version &>/dev/null; then
    echo "   Installing Docker Compose plugin..."
    apt-get install -y docker-compose-plugin 2>/dev/null || {
        mkdir -p /usr/local/lib/docker/cli-plugins
        ARCH=$(dpkg --print-architecture)
        curl -fsSL \
            "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH}" \
            -o /usr/local/lib/docker/cli-plugins/docker-compose
        chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    }
fi

usermod -aG docker,video,input,audio,tty "$ACTUAL_USER"
echo "   Done"

# ==========================================================
# 2/9  Copy project to /opt/meetingbox
# ==========================================================
echo ""
echo "2/9  Setting up project directory..."

mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/data/config"
mkdir -p "$INSTALL_DIR/data/audio/recordings"
mkdir -p "$INSTALL_DIR/data/transcripts"

rsync -a \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='venv' \
    --exclude='.venv' \
    "$REPO_DIR/" "$INSTALL_DIR/"

chown -R "$ACTUAL_USER:$ACTUAL_USER" "$INSTALL_DIR"

# Create .env from example if it doesn't exist
if [ ! -f "$INSTALL_DIR/.env" ]; then
    if [ -f "$INSTALL_DIR/.env.example" ]; then
        cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
        chown "$ACTUAL_USER:$ACTUAL_USER" "$INSTALL_DIR/.env"
        echo "   Created .env from .env.example (edit API keys before use)"
    fi
fi
echo "   Done"

# ==========================================================
# 3/9  Build frontend (web dashboard)
# ==========================================================
echo ""
echo "3/9  Building frontend..."

if [ -f "$INSTALL_DIR/frontend/dist/index.html" ]; then
    echo "   Frontend already built (frontend/dist/index.html exists), skipping"
elif [ -f "$INSTALL_DIR/frontend/package.json" ]; then
    cd "$INSTALL_DIR/frontend"

    # Install Node.js 20 from nodesource if npm is not available
    # (Do NOT use Debian's npm package -- it has broken deps on arm64/trixie)
    if ! command -v npm &>/dev/null; then
        echo "   Installing Node.js 20 from nodesource..."
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
        apt-get install -y nodejs
    fi

    echo "   Running npm install..."
    sudo -u "$ACTUAL_USER" npm install --no-audit --no-fund 2>&1 | tail -1
    echo "   Running npm run build..."
    sudo -u "$ACTUAL_USER" npm run build 2>&1 | tail -3
    echo "   Done"
else
    echo "   WARNING: frontend/package.json not found, skipping"
fi

# ==========================================================
# 4/9  Auto-login on tty1 (no login prompt visible)
# ==========================================================
echo ""
echo "4/9  Configuring auto-login on tty1..."

AUTOLOGIN_DIR="/etc/systemd/system/getty@tty1.service.d"
mkdir -p "$AUTOLOGIN_DIR"

cat > "$AUTOLOGIN_DIR/autologin.conf" << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $ACTUAL_USER --noclear %I \$TERM
EOF

systemctl daemon-reload
echo "   Done"

# ==========================================================
# 5/9  X11 display configuration
# ==========================================================
echo ""
echo "5/9  Configuring X11 display..."

# Do not force custom Xorg Monitor/Screen config here.
# Forced Screen sections can cause:
#   AddScreen/ScreenInit failed for driver 0
# Let Xorg auto-detect display/driver from the running kernel/KMS stack.
mkdir -p /etc/X11/xorg.conf.d
rm -f /etc/X11/xorg.conf.d/99-meetingbox-display.conf

# Ensure Xorg can open virtual consoles on Debian rootless setups.
# Without this, X can fail with:
#   xf86OpenConsole: Cannot open virtual console 1 (Permission denied)
cat > /etc/X11/Xwrapper.config << 'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF
chown root:root /etc/X11/Xwrapper.config
chmod 644 /etc/X11/Xwrapper.config

# Make sure the Xorg wrapper is setuid-root if present.
if [ -f /usr/lib/xorg/Xorg.wrap ]; then
    chown root:root /usr/lib/xorg/Xorg.wrap
    chmod u+s /usr/lib/xorg/Xorg.wrap
fi

# .xinitrc — runs when startx is called
cat > "$ACTUAL_HOME/.xinitrc" << 'XINITRC'
#!/bin/sh
# Disable screen blanking and power management
xset s off
xset -dpms
xset s noblank

# Allow Docker containers to draw on this X display
xhost +local:

# Hide the mouse cursor
unclutter -idle 0 -root &

# Keep X alive (the device-ui Docker container renders to this display)
while true; do
    sleep 60
done
XINITRC

chmod +x "$ACTUAL_HOME/.xinitrc"
chown "$ACTUAL_USER:$ACTUAL_USER" "$ACTUAL_HOME/.xinitrc"

# .bashrc — auto-start X on tty1 login (only if X is not already running)
# Keep retrying and log failures so the device never sits at a shell prompt
# silently if X fails once during boot.
BASHRC="$ACTUAL_HOME/.bashrc"
# Remove any previous legacy block and marker-based block, then write fresh.
sed -i '/^# MeetingBox: auto-start X on tty1 (production kiosk mode)$/,/^fi$/d' "$BASHRC" 2>/dev/null || true
sed -i '/^# >>> MeetingBox kiosk autostart >>>$/,/^# <<< MeetingBox kiosk autostart <<</d' "$BASHRC" 2>/dev/null || true

cat >> "$BASHRC" << 'XSTART'

# >>> MeetingBox kiosk autostart >>>
# MeetingBox: auto-start X on tty1 (production kiosk mode)
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    LOG_FILE="$HOME/meetingbox-startx.log"
    while true; do
        echo "$(date -Is) Starting X..." >> "$LOG_FILE"
        startx -- -nocursor >> "$LOG_FILE" 2>&1
        RC=$?
        echo "$(date -Is) startx exited with code $RC, retrying in 2s" >> "$LOG_FILE"
        sleep 2
    done
fi
# <<< MeetingBox kiosk autostart <<<
XSTART
echo "   Installed/updated startx trigger in .bashrc"

echo "   Done"

# ==========================================================
# 6/9  Hide boot console (kernel quiet + Plymouth splash)
# ==========================================================
echo ""
echo "6/9  Configuring silent boot..."

# Kernel command line on Debian-family kiosk images may live in one of
# these paths. If neither exists, boot quieting is skipped.
CMDLINE=""
for candidate in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
    if [ -f "$candidate" ]; then
        CMDLINE="$candidate"
        break
    fi
done

if [ -n "$CMDLINE" ]; then
    cp "$CMDLINE" "${CMDLINE}.bak"

    # Read current line, strip existing quiet/splash/logo args, then re-add
    CURRENT=$(cat "$CMDLINE")
    CLEANED=$(echo "$CURRENT" | sed \
        -e 's/ quiet//g' \
        -e 's/ splash//g' \
        -e 's/ loglevel=[0-9]*//g' \
        -e 's/ logo.nologo//g' \
        -e 's/ vt.global_cursor_default=[0-9]*//g' \
        -e 's/ plymouth.ignore-serial-consoles//g' \
        -e 's/ console=tty[0-9]*//g')

    echo "${CLEANED} quiet splash loglevel=0 logo.nologo vt.global_cursor_default=0 plymouth.ignore-serial-consoles console=tty1" > "$CMDLINE"
    echo "   Updated $CMDLINE"
else
    echo "   WARNING: Could not find cmdline.txt — skipping kernel quiet boot"
fi

# Plymouth — simple black theme with text
plymouth-set-default-theme -R tribar 2>/dev/null || \
    plymouth-set-default-theme -R text 2>/dev/null || \
    echo "   Plymouth theme set skipped (will fall back to black screen)"

echo "   Done"

# ==========================================================
# 7/9  Systemd services
# ==========================================================
echo ""
echo "7/9  Installing systemd services..."

# Main service — uses production compose override
cat > /etc/systemd/system/meetingbox.service << EOF
[Unit]
Description=MeetingBox (all services via Docker Compose)
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=$ACTUAL_USER
WorkingDirectory=$INSTALL_DIR
ExecStartPre=/bin/bash -c 'until docker info >/dev/null 2>&1; do sleep 2; done'
ExecStart=/usr/bin/docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen up -d
ExecStop=/usr/bin/docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

# Dedicated X server service for kiosk mode.
# This is more reliable than shell-based startx from .bashrc/.profile.
cat > /etc/systemd/system/meetingbox-x.service << EOF
[Unit]
Description=MeetingBox X server on tty1
After=systemd-user-sessions.service plymouth-quit-wait.service
Conflicts=getty@tty1.service

[Service]
User=$ACTUAL_USER
WorkingDirectory=$ACTUAL_HOME
Environment=HOME=$ACTUAL_HOME
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal
StandardError=journal
ExecStart=/usr/bin/xinit $ACTUAL_HOME/.xinitrc -- :0 -nocursor vt1 -keeptty
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

# Onboarding service (first-boot hotspot + setup portal)
# Skip onboarding if setup is complete OR WiFi is already connected.
cat > /etc/systemd/system/meetingbox-onboard.service << EOF
[Unit]
Description=MeetingBox First-Time Onboarding (Hotspot + Setup Portal)
After=NetworkManager.service
Before=meetingbox.service
ConditionPathExists=!/opt/meetingbox/data/config/.setup_complete

[Service]
Type=forking
ExecCondition=/bin/bash -c '! nmcli -t -f TYPE,STATE dev | grep -q "^wifi:connected$"'
ExecStartPre=/bin/bash /opt/meetingbox/scripts/hotspot.sh start
ExecStart=/usr/bin/python3 /opt/meetingbox/scripts/onboard_server.py
ExecStop=/bin/bash /opt/meetingbox/scripts/hotspot.sh stop
Restart=on-failure
RestartSec=5
TimeoutStartSec=30

[Install]
WantedBy=multi-user.target
EOF
chmod +x "$INSTALL_DIR/scripts/hotspot.sh"
chmod +x "$INSTALL_DIR/scripts/onboard_server.py" 2>/dev/null || true

systemctl daemon-reload
systemctl enable meetingbox.service
systemctl enable meetingbox-x.service
systemctl enable meetingbox-onboard.service
systemctl disable getty@tty1.service 2>/dev/null || true

# Enable mDNS
systemctl enable avahi-daemon
systemctl start avahi-daemon 2>/dev/null || true

echo "   Done"

# ==========================================================
# 8/9  Build Docker images
# ==========================================================
echo ""
echo "8/9  Building Docker images (this may take several minutes)..."

cd "$INSTALL_DIR"

# Stop any existing containers
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen down 2>/dev/null || true

# Build all images including device-ui
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen build

echo "   Done"

# ==========================================================
# 9/9  Verify
# ==========================================================
echo ""
echo "9/9  Quick verification..."

# Start containers briefly to test
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen up -d
sleep 15

echo ""
if curl -sf http://localhost:8000/health | grep -q "healthy"; then
    echo "   OK  Backend is healthy"
else
    echo "   --  Backend not responding yet (may still be starting)"
fi

if docker ps --format '{{.Names}}' | grep -q meetingbox-ui; then
    echo "   OK  Device UI container is running"
else
    echo "   --  Device UI not running (X11 may not be active yet — will work after reboot)"
fi

if docker ps --format '{{.Names}}' | grep -q meetingbox-ollama; then
    echo "   OK  Ollama container is running"
else
    echo "   --  Ollama not running"
fi

CONTAINER_COUNT=$(docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen ps -q 2>/dev/null | wc -l)
echo "   --  $CONTAINER_COUNT containers running"

# ==========================================================
# Done
# ==========================================================
echo ""
echo "============================================"
echo "  Production deployment complete!"
echo "============================================"
echo ""
echo "  Install dir:   $INSTALL_DIR"
echo "  Web dashboard: http://meetingbox.local"
echo ""
echo "  What happens on boot:"
echo "    1. Kernel boots silently (black screen)"
echo "    2. Auto-login on tty1 as '$ACTUAL_USER'"
echo "    3. X11 starts automatically (no desktop, just display server)"
echo "    4. Docker Compose starts all services (including device-ui)"
echo "    5. Device UI renders MeetingBox splash on the attached display"
echo ""
echo "  No SSH, no manual commands, no visible console."
echo ""
echo "  Reboot now to test:  sudo reboot"
echo ""
echo "  Troubleshooting (via SSH if needed):"
echo "    docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen logs -f"
echo "    docker logs meetingbox-ui"
echo "    journalctl -u meetingbox.service"
echo ""
