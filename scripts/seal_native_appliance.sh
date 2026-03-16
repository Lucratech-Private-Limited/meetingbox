#!/bin/bash
set -euo pipefail

# Hardens an Ubuntu mini-PC into a more sealed MeetingBox appliance.
# Safe defaults:
# - hide boot menus and shell prompts
# - disable suspend/hibernate
# - disable tty1 shell and common desktop-facing services
# - preserve one opt-in maintenance path via /etc/meetingbox/maintenance-mode

if [ "${EUID}" -ne 0 ]; then
  echo "Run as root: sudo bash scripts/seal_native_appliance.sh"
  exit 1
fi

ENV_DIR="/etc/meetingbox"
mkdir -p "$ENV_DIR"

touch "$ENV_DIR/appliance-mode"

if [ -f /etc/default/grub ]; then
  cp /etc/default/grub /etc/default/grub.meetingbox-sealed.bak
  sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=0/' /etc/default/grub || true
  sed -i 's/^GRUB_TIMEOUT_STYLE=.*/GRUB_TIMEOUT_STYLE=hidden/' /etc/default/grub || true
  sed -i 's/^GRUB_RECORDFAIL_TIMEOUT=.*/GRUB_RECORDFAIL_TIMEOUT=0/' /etc/default/grub || true
  sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT="quiet splash loglevel=3 vt.global_cursor_default=0 systemd.show_status=0 rd.udev.log_level=3"/' /etc/default/grub || true
  update-grub || true
fi

systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
systemctl mask ctrl-alt-del.target || true
systemctl set-default multi-user.target

systemctl disable --now getty@tty1.service || true
systemctl mask getty@tty1.service || true

for svc in \
  apport.service \
  whoopsie.service \
  cups.service \
  cups-browsed.service \
  bluetooth.service \
  ModemManager.service \
  packagekit.service \
  packagekit-offline-update.service \
  ua-reboot-cmds.service \
  motd-news.service; do
  systemctl disable --now "$svc" 2>/dev/null || true
done

for timer in \
  apt-daily.timer \
  apt-daily-upgrade.timer \
  fwupd-refresh.timer \
  motd-news.timer; do
  systemctl disable --now "$timer" 2>/dev/null || true
done

mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/meetingbox.conf <<'EOF'
[Journal]
SystemMaxUse=200M
RuntimeMaxUse=100M
MaxRetentionSec=14day
EOF
systemctl restart systemd-journald

mkdir -p /etc/tmpfiles.d
cat > /etc/tmpfiles.d/meetingbox.conf <<'EOF'
d /var/log/meetingbox 0755 root root -
EOF

mkdir -p /etc/systemd/system/meetingbox.target.d
cat > /etc/systemd/system/meetingbox.target.d/restart-policy.conf <<'EOF'
[Unit]
StartLimitIntervalSec=300
StartLimitBurst=10
EOF

cat > /usr/local/bin/meetingbox-enter-maintenance <<'EOF'
#!/bin/bash
set -euo pipefail
mkdir -p /etc/meetingbox
touch /etc/meetingbox/maintenance-mode
systemctl enable meetingbox-maintenance.service
echo "Maintenance mode enabled. Reboot to get a root shell on tty2."
EOF
chmod +x /usr/local/bin/meetingbox-enter-maintenance

cat > /usr/local/bin/meetingbox-exit-maintenance <<'EOF'
#!/bin/bash
set -euo pipefail
rm -f /etc/meetingbox/maintenance-mode
systemctl disable meetingbox-maintenance.service || true
echo "Maintenance mode disabled. Reboot to return to sealed appliance mode."
EOF
chmod +x /usr/local/bin/meetingbox-exit-maintenance

echo ""
echo "MeetingBox appliance hardening complete."
echo ""
echo "Sealed behavior:"
echo "  - no tty1 shell"
echo "  - no suspend/hibernate"
echo "  - reduced Ubuntu background services"
echo "  - hidden GRUB/menu noise"
echo ""
echo "Recovery path:"
echo "  /usr/local/bin/meetingbox-enter-maintenance"
echo "  reboot"
