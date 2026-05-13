#!/usr/bin/env bash
# Idempotent: ensure GDM auto-logs straight into meetingbox-kiosk (no Ubuntu session chooser).
# Sourced / called from install-gdm-kiosk-session.sh only.

set -euo pipefail

GDM_CUSTOM="${1:-/etc/gdm3/custom.conf}"
RUN_AS_USER="${2:?user}"

if [[ ! -f "$GDM_CUSTOM" ]]; then
  echo "patch-gdm: missing $GDM_CUSTOM" >&2
  exit 1
fi

cp -a "$GDM_CUSTOM" "${GDM_CUSTOM}.bak-meetingbox-$(date +%s)"

# Drop any previous MeetingBox-appended block so re-runs do not duplicate.
if grep -q '^# --- MeetingBox kiosk autologin ---' "$GDM_CUSTOM"; then
  sed -i '/^# --- MeetingBox kiosk autologin ---$/,/^# --- end MeetingBox ---$/d' "$GDM_CUSTOM"
fi

cat >>"$GDM_CUSTOM" <<EOF

# --- MeetingBox kiosk autologin ---
# Auto-login straight into X session "meetingbox-kiosk" (no GNOME, no session picker).
[daemon]
WaylandEnable=false
AutomaticLoginEnable=true
AutomaticLogin=$RUN_AS_USER
AutomaticLoginSession=meetingbox-kiosk
# --- end MeetingBox ---
EOF

echo "patch-gdm: appended kiosk autologin to $GDM_CUSTOM (backup beside it)"
