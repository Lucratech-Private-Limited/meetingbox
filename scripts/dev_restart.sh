#!/bin/bash
# ============================================================================
# MeetingBox Dev Restart
#
# Single command to pull latest code, rebuild, and start everything fresh
# including the OLED screen UI and onboarding hotspot.
#
# Usage:
#   cd ~/meetingbox && sudo bash scripts/dev_restart.sh
#
# Options:
#   --fresh    Reset onboarding (remove .setup_complete marker)
#   --no-build Skip Docker image rebuild (faster if only config changed)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

FRESH=false
BUILD=true

for arg in "$@"; do
    case $arg in
        --fresh)   FRESH=true ;;
        --no-build) BUILD=false ;;
    esac
done

echo "=========================================="
echo "  MeetingBox Dev Restart"
echo "=========================================="
echo "  Project: $PROJECT_DIR"
echo "  Fresh onboarding: $FRESH"
echo "  Rebuild images: $BUILD"
echo ""

# 1. Stop everything
echo "[1/8] Stopping containers and services..."
docker stop meetingbox-ui 2>/dev/null || true
docker rm meetingbox-ui 2>/dev/null || true
docker compose down 2>/dev/null || true
pkill -f onboard_server 2>/dev/null || true
bash scripts/hotspot.sh stop 2>/dev/null || true

# Kill anything on port 80 and WAIT until it's actually free
if ss -tlnp 2>/dev/null | grep -q ':80 '; then
    echo "       Killing process on port 80..."
    fuser -k 80/tcp 2>/dev/null || true
    for i in 1 2 3 4 5; do
        sleep 1
        ss -tlnp 2>/dev/null | grep -q ':80 ' || break
    done
    if ss -tlnp 2>/dev/null | grep -q ':80 '; then
        echo "       WARNING: port 80 still in use after 5 retries"
    else
        echo "       Port 80 is free"
    fi
fi

# 2. Pull latest code
echo "[2/8] Pulling latest code..."
ACTUAL_USER=${SUDO_USER:-$USER}
sudo -u "$ACTUAL_USER" git pull || echo "   (git pull skipped)"

# 2.5. Build frontend
echo "       Building frontend..."
if [ -f "$PROJECT_DIR/frontend/package.json" ]; then
    sudo -u "$ACTUAL_USER" bash -c "cd '$PROJECT_DIR/frontend' && npm install && npm run build"
else
    echo "       WARNING: frontend/package.json not found — skipping build"
fi

# Determine setup state early so steps 4-5 can adapt
MARKER="$PROJECT_DIR/data/config/.setup_complete"
WIFI_CONNECTED=false
if nmcli -t -f TYPE,STATE dev 2>/dev/null | grep -q "^wifi:connected$"; then
    WIFI_CONNECTED=true
fi

# 3. Fresh onboarding reset
if [ "$FRESH" = true ]; then
    echo "[3/8] Resetting onboarding state..."
    rm -f "$MARKER"
    rm -f /opt/meetingbox/data/config/.setup_complete 2>/dev/null || true
else
    echo "[3/8] Keeping existing setup state"
fi

# 4. Rebuild if needed
if [ "$BUILD" = true ]; then
    echo "[4/8] Building Docker images..."
    docker compose --profile backend --profile frontend --profile screen build
else
    echo "[4/8] Skipping build (--no-build)"
fi

# 5. Start backend services
if [ ! -f "$MARKER" ] && [ "$FRESH" = false ] && [ "$WIFI_CONNECTED" = true ]; then
    echo "[5/8] WiFi already connected — restoring setup marker and skipping onboarding..."
    mkdir -p "$(dirname "$MARKER")"
    touch "$MARKER"
fi

if [ ! -f "$MARKER" ]; then
    echo "[5/8] Starting backend services (onboarding mode — nginx skipped)..."
    docker compose --profile backend --profile docker-audio up -d
else
    echo "[5/8] Starting backend services..."
    docker compose --profile backend --profile frontend up -d
fi
echo "       Waiting for services to initialise..."
sleep 5

# 6. X11 access
echo "[6/8] Granting X11 access..."
DISPLAY=:0 xhost +local: 2>/dev/null || echo "   (xhost not available — run startx first)"

# 7. Start screen UI
echo "[7/8] Starting device UI..."
docker compose --profile screen up -d device-ui

# 8. Start onboarding if setup not complete
if [ ! -f "$MARKER" ]; then
    echo "[8/8] Starting onboarding (hotspot only)..."

    # nginx was intentionally not started in step 5, so port 80 is free

    # Start the hotspot so phones can connect
    echo "       Starting WiFi hotspot..."
    bash scripts/hotspot.sh start

    echo ""
    echo "  >>> Run the onboard server manually:"
    echo "  >>> sudo python3 $PROJECT_DIR/scripts/onboard_server.py &"
    echo ""
else
    echo "[8/8] Setup already complete — skipping onboarding"
fi

echo ""
echo "=========================================="
echo "  All running!"
echo "=========================================="
echo ""
echo "  UI logs:     docker logs -f meetingbox-ui 2>&1"
echo "  All logs:    docker compose logs -f"
echo "  Stop all:    docker compose --profile screen down"
echo ""
if [ ! -f "$MARKER" ]; then
    echo "  ONBOARDING: Connect phone to MeetingBox hotspot"
    echo "              Open http://192.168.4.1 in browser"
fi
echo ""
