#!/usr/bin/env bash
# GDM X session: no GNOME Shell — black screen, tiny Openbox, then Docker MeetingBox UI.
# Installed as /usr/local/bin/meetingbox-gdm-kiosk-session by install-gdm-kiosk-session.sh
#
# Keep this process running until logout, or GDM will end the session.

set +e

RELEASE="${MEETINGBOX_RELEASE:-$HOME/meetingbox-mini-pc-release}"
if [[ -f /etc/meetingbox/release ]]; then
  RELEASE=$(tr -d '\n' </etc/meetingbox/release)
fi
RELEASE=$(cd "$RELEASE" 2>/dev/null && pwd || echo "$RELEASE")

export PATH="/usr/sbin:/usr/bin:/usr/local/bin:$PATH"

# Lock panel mode + rotation (DSI / HDMI) — see /etc/meetingbox/panel-xrandr.env
if [[ -x /usr/local/bin/meetingbox-apply-kiosk-display-orientation ]]; then
  /usr/local/bin/meetingbox-apply-kiosk-display-orientation
elif [[ -f "$RELEASE/scripts/apply-kiosk-display-orientation.sh" ]]; then
  bash "$RELEASE/scripts/apply-kiosk-display-orientation.sh"
fi

# Solid black while Docker / UI start (no Ubuntu wallpaper or dock).
xsetroot -solid '#000000' 2>/dev/null || true

# Minimal WM so SDL/Kivy fullscreen behaves; ~2 MB RAM vs full GNOME.
if command -v openbox >/dev/null 2>&1; then
  openbox >/dev/null 2>&1 &
  sleep 0.2
fi

echo "meetingbox-gdm-kiosk: waiting for Docker (max ~15s)..."
for _ in $(seq 1 60); do
  docker info &>/dev/null && break
  sleep 0.25
done

KOISK="$RELEASE/scripts/kiosk-compose-up.sh"
if [[ -f "$KOISK" ]]; then
  # Run compose in the background so this X session reaches a steady state immediately
  # (black + Openbox). First-time image pulls can take minutes; blocking here does not
  # speed Docker up and can make GDM look "stuck" before tail -f.
  # shellcheck disable=SC1090
  (
    bash "$KOISK" "$RELEASE" 2>&1 | logger -t meetingbox-kiosk-compose
  ) &
  disown "$!" 2>/dev/null || true
else
  logger -t meetingbox-kiosk "missing $KOISK — set path in /etc/meetingbox/release"
fi

# Hold the X session open (required by GDM).
exec tail -f /dev/null
