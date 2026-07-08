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
exec "$@"
