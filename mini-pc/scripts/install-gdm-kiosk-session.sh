#!/usr/bin/env bash
# Install a minimal GDM session so the device boots straight into MeetingBox (no GNOME Shell).
#
#   sudo bash scripts/install-gdm-kiosk-session.sh [/home/meetingbox/meetingbox-mini-pc-release]
#
# After install: run install-boot-service.sh, then reboot (see setup-infotainment-kiosk.sh).

set -euo pipefail

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
MINI_PC_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
APPLIANCE_DIR="${1:-$MINI_PC_ROOT}"
RUN_AS_USER="${SUDO_USER:-${MEETINGBOX_USER:-meetingbox}}"

if ! getent passwd "$RUN_AS_USER" &>/dev/null; then
  echo "User not found: $RUN_AS_USER" >&2
  exit 1
fi

APPLIANCE_DIR=$(cd "$APPLIANCE_DIR" && pwd)

# apt cannot run in parallel; boot or unattended-upgrades often holds the lock.
# Do not pgrep "unattended-upgrade" broadly — it matches unattended-upgrade-shutdown
# (idle on many systems) and would block forever. A real upgrade uses apt-get/apt/dpkg
# and/or holds the fuser-checked lock files.
_apt_is_busy() {
  pgrep -x apt-get >/dev/null 2>&1 && return 0
  pgrep -x apt >/dev/null 2>&1 && return 0
  pgrep -x dpkg >/dev/null 2>&1 && return 0
  if command -v fuser >/dev/null 2>&1; then
    local L
    for L in /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/cache/apt/archives/lock; do
      [[ -e "$L" ]] || continue
      fuser "$L" >/dev/null 2>&1 && return 0
    done
  fi
  return 1
}

wait_for_apt_idle() {
  local max="${1:-900}" t=0
  while (( t < max )); do
    if ! _apt_is_busy; then
      [[ "$t" -eq 0 ]] || echo "apt is idle after ${t}s."
      return 0
    fi
    if [[ "$t" -eq 0 ]]; then
      echo "Another apt/dpkg is running — waiting up to ${max}s for the lock to clear..."
    elif (( t % 30 == 0 )); then
      echo "  ... still waiting (${t}s). Check: ps aux | grep -E 'apt-get|dpkg' (ignore unattended-upgrade-shutdown)"
    fi
    sleep 3
    t=$((t + 3))
  done
  echo "Timed out waiting for apt. Finish the other update, then re-run this script." >&2
  return 1
}

wait_for_apt_idle
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openbox x11-xserver-utils xinput

install -m 0755 "$MINI_PC_ROOT/scripts/gdm-kiosk-session.sh" /usr/local/bin/meetingbox-gdm-kiosk-session
install -m 0755 "$MINI_PC_ROOT/scripts/apply-kiosk-display-orientation.sh" /usr/local/bin/meetingbox-apply-kiosk-display-orientation
install -m 0644 "$MINI_PC_ROOT/kiosk-desktop/meetingbox-kiosk.desktop" /usr/share/xsessions/meetingbox-kiosk.desktop

mkdir -p /etc/meetingbox
if [[ ! -f /etc/meetingbox/panel-xrandr.env ]]; then
  install -m 0644 "$MINI_PC_ROOT/kiosk-desktop/panel-xrandr.env.example" /etc/meetingbox/panel-xrandr.env
  echo "Installed /etc/meetingbox/panel-xrandr.env (DSI-1 800x1280 rotate left). Edit to change panel."
fi
echo "$APPLIANCE_DIR" >/etc/meetingbox/release
chmod 644 /etc/meetingbox/release

ASU="/var/lib/AccountsService/users/$RUN_AS_USER"
if [[ -f "$ASU" ]]; then
  cp -a "$ASU" "${ASU}.bak-meetingbox-$(date +%s)"
  if grep -q '^XSession=' "$ASU" 2>/dev/null; then
    sed -i 's/^XSession=.*/XSession=meetingbox-kiosk/' "$ASU"
  else
    printf '\nXSession=meetingbox-kiosk\n' >>"$ASU"
  fi
  echo "Set default X session for $RUN_AS_USER to meetingbox-kiosk in $ASU"
else
  # GDM uses AccountsService for default X session; the file is often missing until first GUI login.
  mkdir -p "$(dirname "$ASU")"
  cat >"$ASU" <<ASUEOF
[User]
XSession=meetingbox-kiosk
SystemAccount=false
ASUEOF
  chmod 644 "$ASU"
  echo "Created $ASU with XSession=meetingbox-kiosk (user had not logged in on the panel before)."
fi

# Pick up XSession= without requiring another reboot (GDM reads AccountsService at login).
if systemctl is-enabled accounts-daemon &>/dev/null; then
  systemctl reload-or-restart accounts-daemon.service 2>/dev/null || true
fi

# GDM: skip Ubuntu greeter / session list — jump straight into meetingbox-kiosk X session.
if [[ -f /etc/gdm3/custom.conf ]]; then
  bash "$MINI_PC_ROOT/scripts/patch-gdm-autologin-kiosk.sh" /etc/gdm3/custom.conf "$RUN_AS_USER"
else
  echo "WARNING: /etc/gdm3/custom.conf missing — set AutomaticLogin + AutomaticLoginSession=meetingbox-kiosk manually."
fi

echo ""
echo "Done."
echo "  1) sudo bash scripts/install-boot-service.sh   # REQUIRED: starts redis+audio without X; starts full stack after graphical"
echo "     Do NOT disable meetingbox-appliance until the kiosk session is verified — otherwise nothing starts Docker."
echo "  2) Reboot — auto-login should go to black screen + app, not the full Ubuntu dock."
echo "  3) If stuck: bash scripts/recovery-appliance-ssh.sh  (from SSH)"
echo "  4) Normal Ubuntu again: remove MeetingBox block from /etc/gdm3/custom.conf; set XSession=ubuntu in $ASU"
echo ""
echo "Still see GDM flash? Optional: scripts/install-xinit-no-gdm.sh (advanced)."
echo "Kiosk session starts Docker in the background; follow progress with:"
echo "  journalctl -t meetingbox-kiosk-compose -f"
