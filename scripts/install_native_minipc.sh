#!/bin/bash
set -euo pipefail

# Native Ubuntu mini-PC installer for MeetingBox.
# Replaces the Raspberry Pi + Docker boot flow with:
# - native Redis
# - native Ollama
# - native Python services under systemd
# - native X server + Kivy touchscreen UI

if [ "${EUID}" -ne 0 ]; then
  echo "Run as root: sudo bash scripts/install_native_minipc.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR="/opt/meetingbox"
ENV_DIR="/etc/meetingbox"
ENV_FILE="$ENV_DIR/meetingbox.env"
RUNTIME_DIR="$INSTALL_DIR/runtime"
VENV_DIR="$INSTALL_DIR/.venvs"
WHISPER_ROOT="$RUNTIME_DIR/whisper.cpp"

ACTUAL_USER="${SUDO_USER:-meetingbox}"
if ! id "$ACTUAL_USER" >/dev/null 2>&1; then
  useradd -m -s /usr/sbin/nologin "$ACTUAL_USER"
fi
ACTUAL_HOME="$(eval echo "~$ACTUAL_USER")"

echo "============================================"
echo " MeetingBox Native Ubuntu Installer"
echo "============================================"
echo "User:        $ACTUAL_USER"
echo "Install dir: $INSTALL_DIR"
echo ""

apt-get update
apt-get install -y --no-install-recommends \
  build-essential \
  cmake \
  curl \
  ffmpeg \
  git \
  redis-server \
  network-manager \
  avahi-daemon \
  python3 \
  python3-dev \
  python3-venv \
  pkg-config \
  portaudio19-dev \
  libportaudio2 \
  libasound2-dev \
  xserver-xorg \
  xinit \
  x11-xserver-utils \
  xserver-xorg-input-libinput \
  unclutter \
  libgl1-mesa-dev \
  libgles2-mesa-dev \
  libsdl2-dev \
  libsdl2-image-dev \
  libsdl2-mixer-dev \
  libsdl2-ttf-dev \
  libmtdev-dev \
  libx11-dev \
  libxrandr-dev \
  libxinerama-dev \
  libxcursor-dev \
  libxi-dev \
  libjpeg-dev \
  zlib1g-dev \
  libfreetype6-dev \
  wget \
  rsync

if ! command -v npm >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi

usermod -aG audio,video,input,tty,network "$ACTUAL_USER"
usermod -s /usr/sbin/nologin "$ACTUAL_USER"

mkdir -p "$INSTALL_DIR" "$ENV_DIR" "$RUNTIME_DIR" "$VENV_DIR"
mkdir -p \
  "$INSTALL_DIR/data/audio/temp" \
  "$INSTALL_DIR/data/audio/recordings" \
  "$INSTALL_DIR/data/transcripts" \
  "$INSTALL_DIR/data/config" \
  "$INSTALL_DIR/logs"

rsync -a \
  --delete \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='venv' \
  --exclude='.venv' \
  "$REPO_DIR/" "$INSTALL_DIR/"

chown -R "$ACTUAL_USER:$ACTUAL_USER" "$INSTALL_DIR"

if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
MEETINGBOX_ROOT=$INSTALL_DIR
MEETINGBOX_DB_PATH=$INSTALL_DIR/data/transcripts/meetings.db
DEVICE_SETTINGS_PATH=$INSTALL_DIR/data/config/device_settings.json
TEMP_SEGMENTS_DIR=$INSTALL_DIR/data/audio/temp
RECORDINGS_DIR=$INSTALL_DIR/data/audio/recordings
REDIS_HOST=127.0.0.1
OLLAMA_HOST=http://127.0.0.1:11434
LOCAL_LLM_MODEL=phi3:mini
USE_LOCAL_LLM=true
WHISPER_ROOT=$WHISPER_ROOT
WHISPER_BIN=$WHISPER_ROOT/build/bin/whisper-cli
WHISPER_MODEL_PATH=$WHISPER_ROOT/models/ggml-medium.bin
WHISPER_THREADS=4
APP_BASE_URL=http://localhost:8000
FRONTEND_BASE_URL=http://localhost:8000
STATIC_DIR=$INSTALL_DIR/frontend/dist
BACKEND_URL=http://localhost:8000
BACKEND_WS_URL=ws://localhost:8000/ws
DISPLAY=:0
DISPLAY_WIDTH=480
DISPLAY_HEIGHT=320
FULLSCREEN=1
LOG_TO_CONSOLE=0
LOG_LEVEL=INFO
JWT_SECRET_KEY=change-me-before-production
# ANTHROPIC_API_KEY=
# GOOGLE_CLIENT_ID=
# GOOGLE_CLIENT_SECRET=
EOF

  if [ -f "$REPO_DIR/.env" ]; then
    grep -E '^(ANTHROPIC_API_KEY|JWT_SECRET_KEY|GOOGLE_CLIENT_ID|GOOGLE_CLIENT_SECRET|AI_MODEL|LOCAL_LLM_MODEL|USE_LOCAL_LLM)=' "$REPO_DIR/.env" >> "$ENV_FILE" || true
  fi
fi
chmod 640 "$ENV_FILE"
chown root:"$ACTUAL_USER" "$ENV_FILE"

install_python_service() {
  local name="$1"
  local req="$2"
  local venv="$VENV_DIR/$name"
  python3 -m venv "$venv"
  "$venv/bin/pip" install --upgrade pip wheel setuptools
  "$venv/bin/pip" install -r "$req"
}

install_python_service "web" "$INSTALL_DIR/services/web/requirements.txt"
install_python_service "audio" "$INSTALL_DIR/services/audio/requirements.txt"
install_python_service "transcription" "$INSTALL_DIR/services/transcription/requirements.txt"
install_python_service "ai" "$INSTALL_DIR/services/ai/requirements.txt"
install_python_service "device-ui" "$INSTALL_DIR/device-ui/requirements.txt"

if [ ! -d "$WHISPER_ROOT/.git" ]; then
  sudo -u "$ACTUAL_USER" git clone https://github.com/ggerganov/whisper.cpp.git "$WHISPER_ROOT"
else
  sudo -u "$ACTUAL_USER" git -C "$WHISPER_ROOT" pull --ff-only
fi
sudo -u "$ACTUAL_USER" cmake -S "$WHISPER_ROOT" -B "$WHISPER_ROOT/build"
sudo -u "$ACTUAL_USER" cmake --build "$WHISPER_ROOT/build" --config Release -j"$(nproc)"

if [ ! -f "$WHISPER_ROOT/models/ggml-medium.bin" ]; then
  sudo -u "$ACTUAL_USER" mkdir -p "$WHISPER_ROOT/models"
  sudo -u "$ACTUAL_USER" wget -O "$WHISPER_ROOT/models/ggml-medium.bin" \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin
fi

cd "$INSTALL_DIR/frontend"
sudo -u "$ACTUAL_USER" npm install --no-audit --no-fund
sudo -u "$ACTUAL_USER" npm run build

systemctl enable --now redis-server
systemctl enable --now NetworkManager
systemctl enable --now avahi-daemon
systemctl enable --now ollama

LOCAL_LLM_MODEL="$(grep '^LOCAL_LLM_MODEL=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
if [ -n "$LOCAL_LLM_MODEL" ]; then
  sudo -u "$ACTUAL_USER" env OLLAMA_HOST=http://127.0.0.1:11434 ollama pull "$LOCAL_LLM_MODEL" || true
fi

cat > /etc/X11/Xwrapper.config <<'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF
chmod 644 /etc/X11/Xwrapper.config

if [ -f /usr/lib/xorg/Xorg.wrap ]; then
  chown root:root /usr/lib/xorg/Xorg.wrap
  chmod u+s /usr/lib/xorg/Xorg.wrap
fi

cat > "$ACTUAL_HOME/.xinitrc" <<'EOF'
#!/bin/sh
xset s off
xset -dpms
xset s noblank
unclutter -idle 0 -root &
while true; do
  sleep 60
done
EOF
chmod +x "$ACTUAL_HOME/.xinitrc"
chown "$ACTUAL_USER:$ACTUAL_USER" "$ACTUAL_HOME/.xinitrc"

cat > /etc/systemd/system/meetingbox-x.service <<EOF
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

cat > /etc/systemd/system/meetingbox-web.service <<EOF
[Unit]
Description=MeetingBox Web API
After=network-online.target redis-server.service ollama.service
Wants=network-online.target redis-server.service ollama.service

[Service]
Type=simple
User=$ACTUAL_USER
WorkingDirectory=$INSTALL_DIR/services/web
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/web/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=meetingbox.target
EOF

cat > /etc/systemd/system/meetingbox-audio.service <<EOF
[Unit]
Description=MeetingBox Audio Capture
After=redis-server.service sound.target
Wants=redis-server.service

[Service]
Type=simple
User=$ACTUAL_USER
WorkingDirectory=$INSTALL_DIR/services/audio
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/audio/bin/python audio_capture.py
Restart=always
RestartSec=3

[Install]
WantedBy=meetingbox.target
EOF

cat > /etc/systemd/system/meetingbox-transcription.service <<EOF
[Unit]
Description=MeetingBox Transcription
After=redis-server.service meetingbox-audio.service
Wants=redis-server.service

[Service]
Type=simple
User=$ACTUAL_USER
WorkingDirectory=$INSTALL_DIR/services/transcription
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/transcription/bin/python transcription_service.py
Restart=always
RestartSec=3

[Install]
WantedBy=meetingbox.target
EOF

cat > /etc/systemd/system/meetingbox-ai.service <<EOF
[Unit]
Description=MeetingBox Summarization and Agentic Actions
After=redis-server.service ollama.service meetingbox-transcription.service
Wants=redis-server.service ollama.service

[Service]
Type=simple
User=$ACTUAL_USER
WorkingDirectory=$INSTALL_DIR/services/ai
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/ai/bin/python ai_service.py
Restart=always
RestartSec=3

[Install]
WantedBy=meetingbox.target
EOF

cat > /etc/systemd/system/meetingbox-ui.service <<EOF
[Unit]
Description=MeetingBox Touchscreen UI
After=meetingbox-x.service meetingbox-web.service
Requires=meetingbox-x.service meetingbox-web.service

[Service]
Type=simple
User=$ACTUAL_USER
WorkingDirectory=$INSTALL_DIR/device-ui
EnvironmentFile=$ENV_FILE
Environment=DISPLAY=:0
Environment=XAUTHORITY=$ACTUAL_HOME/.Xauthority
ExecStart=$VENV_DIR/device-ui/bin/python $INSTALL_DIR/device-ui/src/main.py
Restart=always
RestartSec=2

[Install]
WantedBy=meetingbox.target
EOF

cat > /etc/systemd/system/meetingbox.target <<'EOF'
[Unit]
Description=MeetingBox Native Appliance Stack
Wants=meetingbox-web.service meetingbox-audio.service meetingbox-transcription.service meetingbox-ai.service meetingbox-ui.service
After=redis-server.service ollama.service meetingbox-x.service

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/meetingbox-maintenance.service <<EOF
[Unit]
Description=MeetingBox Maintenance Shell on tty2
After=multi-user.target
ConditionPathExists=/etc/meetingbox/maintenance-mode

[Service]
ExecStart=-/sbin/agetty --autologin root --noclear tty2 linux
TTYPath=/dev/tty2
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty
StandardOutput=tty
StandardError=tty
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
EOF

if [ -f /etc/systemd/system/meetingbox.service ]; then
  systemctl disable meetingbox.service || true
fi
if [ -f /etc/systemd/system/meetingbox-onboard.service ]; then
  systemctl disable meetingbox-onboard.service || true
fi

systemctl disable getty@tty1.service || true
systemctl mask getty@tty1.service || true
systemctl daemon-reload
systemctl enable meetingbox-x.service
systemctl enable meetingbox.target
systemctl disable meetingbox-maintenance.service || true
systemctl restart meetingbox-x.service || true
systemctl restart meetingbox.target || true

if [ -f /etc/default/grub ]; then
  cp /etc/default/grub /etc/default/grub.meetingbox.bak
  sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT="quiet splash loglevel=3 vt.global_cursor_default=0"/' /etc/default/grub || true
  update-grub || true
fi

echo ""
echo "Native MeetingBox install complete."
echo ""
echo "Edit secrets and production values here:"
echo "  $ENV_FILE"
echo ""
echo "Key services:"
echo "  systemctl status meetingbox-x"
echo "  systemctl status meetingbox.target"
echo "  systemctl status meetingbox-ui"
echo ""
echo "Open locally:"
echo "  http://localhost:8000"
echo ""
echo "Recommended next step:"
echo "  reboot"
