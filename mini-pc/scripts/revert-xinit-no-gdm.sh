#!/usr/bin/env bash
# Undo install-xinit-no-gdm.sh — re-enable GDM and graphical boot.

set -euo pipefail

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

rm -f /etc/systemd/system/getty@tty1.service.d/meetingbox-autologin.conf
rmdir /etc/systemd/system/getty@tty1.service.d 2>/dev/null || true

systemctl daemon-reload
systemctl enable gdm3.service
systemctl set-default graphical.target

echo "Removed tty1 autologin override; enabled gdm3; default target graphical.target."
echo "Reboot: sudo reboot"
echo "Optionally remove ~/.profile snippet between MEETINGBOX_XINIT markers by hand."
