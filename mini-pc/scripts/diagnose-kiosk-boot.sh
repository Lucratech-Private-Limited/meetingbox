#!/usr/bin/env bash
# Run on the mini PC (SSH is fine) after "kiosk not happening". Prints checks; no changes.
#   bash scripts/diagnose-kiosk-boot.sh
#   bash scripts/diagnose-kiosk-boot.sh /home/meetingbox/meetingbox-mini-pc-release

set -uo pipefail

REL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${1:-}" ]]; then
  REL="$(cd "$1" && pwd)"
fi
U="${SUDO_USER:-${MEETINGBOX_USER:-$USER}}"

echo "=== MeetingBox kiosk boot diagnostics ==="
echo "Appliance dir: $REL"
echo "GUI user (expected): $U"
echo ""

echo "--- Docker group (required for kiosk session to run compose) ---"
if id -nG "$U" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
  echo "OK: user $U is in group docker"
else
  echo "PROBLEM: user $U is NOT in group docker — run: sudo usermod -aG docker $U then log out/in or reboot"
fi
echo ""

echo "--- Session files ---"
for p in /usr/share/xsessions/meetingbox-kiosk.desktop /usr/local/bin/meetingbox-gdm-kiosk-session; do
  if [[ -e "$p" ]]; then
    echo "OK: $p"
  else
    echo "MISSING: $p — re-run: sudo bash scripts/install-gdm-kiosk-session.sh"
  fi
done
if [[ -f /etc/meetingbox/release ]]; then
  echo "OK: /etc/meetingbox/release -> $(tr -d '\n' </etc/meetingbox/release)"
else
  echo "MISSING: /etc/meetingbox/release — re-run install-gdm-kiosk-session.sh"
fi
echo ""

echo "--- AccountsService default session ---"
ASU="/var/lib/AccountsService/users/$U"
if [[ -f "$ASU" ]]; then
  grep -E '^XSession=' "$ASU" 2>/dev/null || echo "(no XSession= line — add XSession=meetingbox-kiosk)"
else
  echo "MISSING: $ASU — re-run: sudo bash scripts/install-gdm-kiosk-session.sh (creates this file if absent)"
fi
echo ""

echo "--- GDM autologin (MeetingBox block) ---"
for g in /etc/gdm3/custom.conf /etc/gdm/custom.conf; do
  if [[ -f "$g" ]]; then
    echo "File: $g"
    grep -E 'AutomaticLogin|AutomaticLoginSession|WaylandEnable|MeetingBox' "$g" 2>/dev/null | tail -20 || true
  fi
done
echo ""

echo "--- .env compose profiles (device-ui needs mini-pc or screen) ---"
if [[ -f "$REL/.env" ]]; then
  grep -E '^[[:space:]]*COMPOSE_PROFILES=' "$REL/.env" || echo "(no COMPOSE_PROFILES= — kiosk-compose-up.sh now defaults to mini-pc,docker-audio if absent)"
else
  echo "MISSING $REL/.env — copy .env.example to .env"
fi
echo ""

echo "--- Docker containers ---"
if command -v docker >/dev/null 2>&1; then
  docker ps -a 2>/dev/null || sudo docker ps -a 2>/dev/null || echo "(docker ps failed)"
else
  echo "docker not in PATH"
fi
echo ""

echo "--- Recent kiosk / compose logs (this boot) ---"
journalctl -b --no-pager -t meetingbox-kiosk -t meetingbox-kiosk-compose 2>/dev/null | tail -40 || true
echo ""

echo "--- Recent meetingbox-appliance systemd (this boot) ---"
journalctl -b --no-pager -u meetingbox-appliance.service 2>/dev/null | tail -25 || true
echo ""

echo "--- GDM unit ---"
systemctl is-active gdm3.service 2>/dev/null && echo "gdm3.service: active" || true
systemctl is-active gdm.service 2>/dev/null && echo "gdm.service: active" || true
echo ""

echo "=== Next steps ==="
echo "1) Black screen but no app:  journalctl -t meetingbox-kiosk-compose -b"
echo "2) Still Ubuntu desktop:     confirm XSession=meetingbox-kiosk + GDM MeetingBox block above"
echo "3) Start stack manually:     bash $REL/scripts/recovery-appliance-ssh.sh"
echo "4) Re-apply installers:      sudo bash $REL/scripts/setup-infotainment-kiosk.sh && sudo reboot"
