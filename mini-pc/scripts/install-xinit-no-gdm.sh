#!/usr/bin/env bash
# Aggressive kiosk: disable GDM entirely — tty1 auto-login runs startx → MeetingBox only.
# You will NOT see the Ubuntu/GDM login UI. You WILL still see BIOS/kernel unless quiet splash is tuned.
#
#   MEETINGBOX_I_KNOW=1 sudo bash scripts/install-xinit-no-gdm.sh [/path/to/release]
#
# Revert: sudo bash scripts/revert-xinit-no-gdm.sh
#
# Requires SSH or physical console if something breaks. Keep another machine handy.

set -euo pipefail

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Run as root: sudo MEETINGBOX_I_KNOW=1 $0" >&2
  exit 1
fi

if [[ "${MEETINGBOX_I_KNOW:-}" != "1" ]]; then
  echo "This disables GDM and boots to startx on tty1. Type exactly:" >&2
  echo "  MEETINGBOX_I_KNOW=1 sudo $0" >&2
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

RUN_AS_HOME=$(getent passwd "$RUN_AS_USER" | cut -d: -f6)
APPLIANCE_DIR=$(cd "$APPLIANCE_DIR" && pwd)

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  xinit xorg x11-xserver-utils openbox

mkdir -p /etc/meetingbox
echo "$APPLIANCE_DIR" >/etc/meetingbox/release
chmod 644 /etc/meetingbox/release

install -m 0755 "$MINI_PC_ROOT/scripts/gdm-kiosk-session.sh" /usr/local/bin/meetingbox-gdm-kiosk-session
install -m 0644 "$MINI_PC_ROOT/kiosk-desktop/meetingbox-kiosk.desktop" /usr/share/xsessions/meetingbox-kiosk.desktop
install -m 0755 "$MINI_PC_ROOT/kiosk-desktop/xinitrc-meetingbox" "$RUN_AS_HOME/.xinitrc-meetingbox"
chown "$RUN_AS_USER:$RUN_AS_USER" "$RUN_AS_HOME/.xinitrc-meetingbox"

GETTY_DROP=/etc/systemd/system/getty@tty1.service.d
mkdir -p "$GETTY_DROP"
cat >"$GETTY_DROP/meetingbox-autologin.conf" <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $RUN_AS_USER --noclear %I \$TERM
EOF

MARK_BEGIN="# --- MEETINGBOX_XINIT_BEGIN ---"
MARK_END="# --- MEETINGBOX_XINIT_END ---"
PROFILE="$RUN_AS_HOME/.profile"
if [[ -f "$PROFILE" ]] && grep -qF "$MARK_BEGIN" "$PROFILE"; then
  echo "Profile already patched."
else
  cp -a "$PROFILE" "${PROFILE}.bak-meetingbox-$(date +%s)" 2>/dev/null || touch "$PROFILE"
  chown "$RUN_AS_USER:$RUN_AS_USER" "$PROFILE"
  cat >>"$PROFILE" <<EOF

$MARK_BEGIN
if [ -z "\${DISPLAY:-}" ] && [ "\$(tty 2>/dev/null)" = "/dev/tty1" ] && [ "\$(id -un)" = "$RUN_AS_USER" ]; then
  exec startx "\$HOME/.xinitrc-meetingbox" -- vt1
fi
$MARK_END
EOF
  chown "$RUN_AS_USER:$RUN_AS_USER" "$PROFILE"
fi

systemctl disable gdm3.service 2>/dev/null || true
systemctl stop gdm3.service 2>/dev/null || true
systemctl set-default multi-user.target
systemctl daemon-reload

echo ""
echo "GDM disabled; default target multi-user. tty1 will auto-login and start X + MeetingBox."
echo "Reboot: sudo reboot"
echo "Revert: sudo bash $MINI_PC_ROOT/scripts/revert-xinit-no-gdm.sh"
