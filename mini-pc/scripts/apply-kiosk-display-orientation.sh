#!/usr/bin/env bash
# Apply panel mode + rotation once X is up (GDM kiosk session).
# Config: /etc/meetingbox/panel-xrandr.env (installed from panel-xrandr.env.example).
# Disable: MEETINGBOX_SKIP_PANEL_XRANDR=1 in that file.
#
# After rotation, map touchscreen to the same output + optional Coordinate Transformation
# Matrix (when taps hit the wrong place). Requires: xinput (apt install xinput).

set +e

[[ -n "${DISPLAY:-}" ]] || export DISPLAY=:0

ENV_FILE="${MEETINGBOX_PANEL_XRANDR_ENV:-/etc/meetingbox/panel-xrandr.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

if [[ "${MEETINGBOX_SKIP_PANEL_XRANDR:-0}" == "1" ]]; then
  exit 0
fi

command -v xrandr >/dev/null 2>&1 || exit 0

OUT="${MEETINGBOX_PANEL_OUTPUT:-DSI-1}"
MODE="${MEETINGBOX_PANEL_MODE:-800x1280}"
ROT="${MEETINGBOX_PANEL_ROTATE:-right}"

XR_OK=0
# One shot: mode + rotation (matches common DSI portrait panels used as landscape).
if xrandr --output "$OUT" --mode "$MODE" --rotate "$ROT" 2>/dev/null; then
  logger -t meetingbox-kiosk "xrandr ok: --output $OUT --mode $MODE --rotate $ROT"
  XR_OK=1
elif xrandr --output "$OUT" --rotate "$ROT" 2>/dev/null; then
  logger -t meetingbox-kiosk "xrandr ok: --output $OUT --rotate $ROT (mode fallback)"
  XR_OK=1
else
  logger -t meetingbox-kiosk "xrandr: could not apply orientation for output '$OUT' (edit $ENV_FILE)"
fi

# Apply libinput-style 3x3 matrix to a device (fixes offset when map-to-output is not enough).
_touch_apply_matrix() {
  local dev="$1"
  [[ -z "$dev" ]] && return 0
  local m=()
  if [[ -n "${MEETINGBOX_TOUCH_COORD_MATRIX:-}" ]]; then
    read -r -a m <<< "${MEETINGBOX_TOUCH_COORD_MATRIX}"
  elif [[ -n "${MEETINGBOX_TOUCH_MATRIX_PRESET:-}" ]]; then
    case "${MEETINGBOX_TOUCH_MATRIX_PRESET}" in
      right) m=(0 1 0 -1 0 1 0 0 1) ;;   # 90° CW — try when panel uses --rotate right
      left) m=(0 -1 1 1 0 0 0 0 1) ;;    # 90° CCW
      inverted) m=(-1 0 1 0 -1 1 0 0 1) ;;
      normal) m=(1 0 0 0 1 0 0 0 1) ;;
    esac
  fi
  if [[ ${#m[@]} -eq 9 ]]; then
    if xinput set-prop "$dev" "Coordinate Transformation Matrix" "${m[@]}" 2>/dev/null; then
      logger -t meetingbox-kiosk "xinput: Coordinate Transformation Matrix on '$dev': ${m[*]}"
    else
      logger -t meetingbox-kiosk "xinput: set Coordinate Transformation Matrix failed for '$dev' (check xinput list-props)"
    fi
  fi
}

_map_one() {
  local devname="$1"
  [[ -z "$devname" ]] && return 1
  if xinput map-to-output "$devname" "$OUT" 2>/dev/null; then
    logger -t meetingbox-kiosk "xinput: map-to-output '$devname' -> $OUT"
    _touch_apply_matrix "$devname"
    return 0
  fi
  return 1
}

# Pointer slave by numeric id (avoids duplicate device names mapping the wrong slave).
_map_one_by_id() {
  local id="$1"
  [[ -z "$id" ]] && return 1
  if xinput map-to-output "$id" "$OUT" 2>/dev/null; then
    logger -t meetingbox-kiosk "xinput: map-to-output id=$id -> $OUT"
    _touch_apply_matrix "$id"
    return 0
  fi
  return 1
}

# Map touchscreen XInput devices to the same output as the panel (critical after --rotate).
_map_touch_to_output() {
  if [[ "${MEETINGBOX_MAP_TOUCH_TO_OUTPUT:-1}" != "1" ]]; then
    return 0
  fi
  command -v xinput >/dev/null 2>&1 || {
    logger -t meetingbox-kiosk "touch: install xinput (apt install xinput) to align touch with display"
    return 0
  }
  local devname mapped=0

  # 0) Numeric id first (best when `xinput list` shows duplicate names — use pointer slave id, e.g. 15)
  if [[ -n "${MEETINGBOX_TOUCH_XINPUT_ID:-}" ]] && [[ "${MEETINGBOX_TOUCH_XINPUT_ID}" =~ ^[0-9]+$ ]]; then
    if _map_one_by_id "${MEETINGBOX_TOUCH_XINPUT_ID}"; then
      mapped=1
    fi
  fi

  # 1) Explicit device name (get exact name from: xinput list --name-only)
  if [[ "$mapped" -eq 0 ]] && [[ -n "${MEETINGBOX_TOUCH_XINPUT_DEVICE:-}" ]]; then
    if _map_one "${MEETINGBOX_TOUCH_XINPUT_DEVICE}"; then
      mapped=1
    fi
  fi
  if [[ -n "${MEETINGBOX_TOUCH_XINPUT_NAMES:-}" ]]; then
    IFS='|' read -r -a _touch_arr <<<"${MEETINGBOX_TOUCH_XINPUT_NAMES}"
    for devname in "${_touch_arr[@]}"; do
      devname="${devname#"${devname%%[![:space:]]*}"}"
      devname="${devname%"${devname##*[![:space:]]}"}"
      [[ -z "$devname" ]] && continue
      if _map_one "$devname"; then
        mapped=1
      fi
    done
  fi

  # 2) Heuristic match (skip if we already mapped explicitly — avoids double-mapping mouse)
  if [[ "$mapped" -eq 0 ]]; then
    while IFS= read -r devname; do
      [[ -z "$devname" ]] && continue
      case "$devname" in
        'Virtual core pointer'|'Virtual core XTEST pointer'|'Virtual core keyboard') continue ;;
      esac
      if _map_one "$devname"; then
        mapped=1
      fi
    done < <(xinput list --name-only 2>/dev/null | grep -iE 'touch|touchscreen|goodix|ilitek|elan|stylus|digitizer|wacom|atmel|zeafte|finger' | grep -viE 'keyboard|video bus' || true)
  fi

  if [[ "$mapped" -eq 0 ]]; then
    logger -t meetingbox-kiosk "touch: no device mapped — on the panel: xinput list --name-only ; set MEETINGBOX_TOUCH_XINPUT_DEVICE in /etc/meetingbox/panel-xrandr.env"
  fi
}

if [[ "$XR_OK" -eq 1 ]]; then
  _map_touch_to_output
fi

exit 0
