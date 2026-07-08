#!/bin/sh
# Optional: read host X11 resolution so Kivy matches the physical panel (see .env.example).
# Do not use "set -e" — a failed xrandr must not block starting Python.
for _mbx in /usr/local/bin/meetingbox-host-reboot /usr/local/bin/meetingbox-host-poweroff; do
  if [ -f "$_mbx" ]; then chmod +x "$_mbx" 2>/dev/null || true; fi
done
if [ "${MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR:-0}" = "1" ]; then
  if command -v xrandr >/dev/null 2>&1; then
    dims=$(xrandr 2>/dev/null | sed -n 's/.*current \([0-9][0-9]*\) x \([0-9][0-9]*\).*/\1 \2/p' | head -n1)
    if [ -n "$dims" ]; then
      export DISPLAY_WIDTH="${dims% *}"
      export DISPLAY_HEIGHT="${dims#* }"
      echo "MeetingBox device-ui: MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1 → ${DISPLAY_WIDTH}x${DISPLAY_HEIGHT}" >&2
    else
      echo "MeetingBox device-ui: xrandr found no 'current WxH' line; keeping DISPLAY_WIDTH/DISPLAY_HEIGHT from env." >&2
    fi
  else
    echo "MeetingBox device-ui: MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1 but xrandr missing; install x11-xserver-utils in image." >&2
  fi
fi

# --- Disable auto-rotation and lock the panel to landscape ---
# GNOME auto-rotates via the host iio-sensor-proxy. Mask it (no sensor = no
# rotation), then set landscape once. Disable with MEETINGBOX_LOCK_LANDSCAPE=0.
if [ "${MEETINGBOX_LOCK_LANDSCAPE:-1}" = "1" ]; then
  sudo -n /bin/sh /usr/local/bin/meetingbox-host-orientation-lock 2>/dev/null || true
  if command -v xrandr >/dev/null 2>&1; then
    _out=$(xrandr 2>/dev/null | awk '/ connected/{print $1; exit}')
    [ -n "$_out" ] && xrandr --output "$_out" --rotate "${MEETINGBOX_PANEL_ROTATE:-right}" 2>/dev/null || true
  fi
fi

exec "$@"
