#!/usr/bin/env bash
# Run on the mini PC with the panel attached. Collects facts for touch/rotation issues.
#   export DISPLAY=:0
#   bash scripts/diagnose-touch-panel.sh
# Paste full output when asking for help.

set +u
export DISPLAY="${DISPLAY:-:0}"

echo "=== MeetingBox touch / panel diagnostics ==="
echo "DATE: $(date -Iseconds 2>/dev/null || date)"
echo "DISPLAY=$DISPLAY"
echo ""

echo "--- xrandr ---"
xrandr 2>&1 || true
echo ""

echo "--- xinput list (full) ---"
xinput list 2>&1 || true
echo ""

echo "--- Goodix / touch lines ---"
xinput list 2>&1 | grep -iE 'goodix|touch|pointer|DSI' || true
echo ""

echo "--- map-to-output test (dry info: device 15 -> DSI-1) ---"
echo "If touch is wrong, pointer id should be slave pointer (2), not keyboard (3)."
echo ""

for id in 15 18; do
  echo "Properties for id=$id:"
  xinput list-props "$id" 2>&1 | grep -iE 'Coordinate Transformation|Device Node|libinput' | head -20 || true
  echo ""
done

echo "--- /etc/meetingbox/panel-xrandr.env (if any) ---"
if [[ -f /etc/meetingbox/panel-xrandr.env ]]; then
  grep -v '^#' /etc/meetingbox/panel-xrandr.env | grep -v '^[[:space:]]*$' || true
else
  echo "(missing)"
fi
echo ""

echo "--- mini-pc .env display / sync (if file exists) ---"
REL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$REL/.env" ]]; then
  grep -E '^MEETINGBOX_SYNC_DISPLAY|^DISPLAY_WIDTH|^DISPLAY_HEIGHT|^DEVICE_UI' "$REL/.env" 2>/dev/null || true
else
  echo "(no $REL/.env)"
fi
echo ""

echo "--- docker device-ui ---"
docker ps -a --filter name=meetingbox-appliance-ui 2>/dev/null || true
echo ""

echo "=== Hints ==="
echo "1) Touch pointer id is usually 15 for Goodix (slave pointer). Id 18 is keyboard slave — do NOT map 18."
echo "2) In panel-xrandr.env set: MEETINGBOX_TOUCH_XINPUT_ID=15"
echo "3) If matrix preset makes it worse, remove MEETINGBOX_TOUCH_MATRIX_PRESET and reboot."
echo "4) Run: xinput test 15   (you should see events when touching the glass)"
