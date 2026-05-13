#!/usr/bin/env bash
# Infotainment-style boot: no Ubuntu desktop — black screen → MeetingBox fullscreen (Docker).
# Runs: install-gdm-kiosk-session.sh + install-boot-service.sh
#
#   cd ~/meetingbox-mini-pc-release
#   cp .env.example .env && nano .env   # BACKEND_URL, COMPOSE_PROFILES=mini-pc,docker-audio, etc.
#   sudo usermod -aG docker meetingbox && newgrp docker   # or re-login once
#   sudo bash scripts/setup-infotainment-kiosk.sh
#   sudo reboot

set -euo pipefail

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Run from the appliance directory:" >&2
  echo "  cd ~/meetingbox-mini-pc-release && sudo bash scripts/setup-infotainment-kiosk.sh" >&2
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
MINI_PC_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
APPLIANCE_DIR="${1:-$MINI_PC_ROOT}"
RUN_AS_USER="${SUDO_USER:-${MEETINGBOX_USER:-meetingbox}}"

APPLIANCE_DIR=$(cd "$APPLIANCE_DIR" && pwd)

if [[ ! -f "$APPLIANCE_DIR/docker-compose.yml" ]]; then
  echo "No docker-compose.yml in $APPLIANCE_DIR" >&2
  exit 1
fi

if [[ ! -f "$APPLIANCE_DIR/.env" ]]; then
  echo "WARNING: $APPLIANCE_DIR/.env missing — copy .env.example to .env and edit before reboot." >&2
fi

if ! id -nG "$RUN_AS_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
  echo "WARNING: user '$RUN_AS_USER' is not in group 'docker'. Run:" >&2
  echo "  sudo usermod -aG docker $RUN_AS_USER" >&2
  echo "then log out and back in (or reboot), then re-run this script if services fail." >&2
fi

echo "=== 1/2 GDM kiosk session (meetingbox-kiosk + autologin) ==="
bash "$SCRIPT_DIR/install-gdm-kiosk-session.sh" "$APPLIANCE_DIR"

echo "=== 2/2 systemd (redis+audio @ boot + full stack after graphical) ==="
bash "$SCRIPT_DIR/install-boot-service.sh" "$APPLIANCE_DIR"

echo ""
echo "Infotainment setup installed."
echo "  • Boot: brief firmware/GDM flash → black screen → MeetingBox (not the Ubuntu dock)."
echo "  • Next: sudo reboot"
echo "  • After future git pulls: re-run this script (or install-gdm-kiosk-session + install-boot-service)"
echo "    so /usr/local/bin/meetingbox-gdm-kiosk-session and systemd units stay current."
echo "  • Compose logs from the kiosk X session: journalctl -t meetingbox-kiosk-compose -b"
echo "  • SSH recovery: bash $APPLIANCE_DIR/scripts/recovery-appliance-ssh.sh"
echo "  • Revert desktop: see INFOTAINMENT.md"
