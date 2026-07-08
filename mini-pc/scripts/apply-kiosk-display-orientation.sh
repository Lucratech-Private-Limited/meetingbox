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

OUT="${MEETINGBOX_PANEL_OUTPUT:-DSI-1}"
MODE="${MEETINGBOX_PANEL_MODE:-800x1280}"
ROT="${MEETINGBOX_PANEL_ROTATE:-left}"

# Try several xrandr forms; return 0 if one succeeds.
_xrandr_try_once() {
  if xrandr --output "$OUT" --mode "$MODE" --rotate "$ROT" 2>/dev/null; then
    return 0
  fi
  if xrandr --output "$OUT" --rotate "$ROT" 2>/dev/null; then
    return 0
  fi
  # Current mode may be wrong name in .env; keep rotation.
  if xrandr --output "$OUT" --auto --rotate "$ROT" 2>/dev/null; then
    return 0
  fi
  return 1
}

XR_OK=0
if command -v xrandr >/dev/null 2>&1; then
  # Optional: pick first connected output from `xrandr` when OUT is wrong (e.g. HDMI-1 vs DSI-1).
  if [[ "${OUT}" == "auto" ]] || [[ "${OUT}" == "AUTO" ]]; then
    picked=$(xrandr 2>/dev/null | awk '/ connected / { print $1; exit }')
    if [[ -n "${picked}" ]]; then
      OUT="$picked"
      logger -t meetingbox-kiosk "xrandr: MEETINGBOX_PANEL_OUTPUT=auto -> using OUT=$OUT"
    else
      logger -t meetingbox-kiosk "xrandr: MEETINGBOX_PANEL_OUTPUT=auto but no connected output yet"
    fi
  fi

  # GDM runs this very early; the X output sometimes appears a few seconds later — retry.
  init_delay="${MEETINGBOX_XRANDR_INITIAL_DELAY:-0}"
  if [[ "${init_delay}" =~ ^[0-9]+$ ]] && [[ "$init_delay" -gt 0 ]]; then
    sleep "$init_delay"
  fi
  attempts="${MEETINGBOX_XRANDR_ATTEMPTS:-30}"
  delay="${MEETINGBOX_XRANDR_RETRY_DELAY:-1}"
  for ((i = 1; i <= attempts; i++)); do
    if _xrandr_try_once; then
      logger -t meetingbox-kiosk "xrandr ok: --output $OUT --mode $MODE --rotate $ROT (attempt $i/$attempts)"
      XR_OK=1
      break
    fi
    if [[ "$i" -eq 1 ]] || [[ "$i" -eq "$attempts" ]]; then
      err=$(xrandr --output "$OUT" --mode "$MODE" --rotate "$ROT" 2>&1 | head -c 400)
      logger -t meetingbox-kiosk "xrandr failed (attempt $i/$attempts) OUT=$OUT: ${err//$'\n'/ }"
    fi
    [[ "$i" -lt "$attempts" ]] && sleep "$delay"
  done

  if [[ "$XR_OK" -ne 1 ]]; then
    logger -t meetingbox-kiosk "xrandr: rotation not applied after $attempts attempts — fix MEETINGBOX_PANEL_OUTPUT / MEETINGBOX_PANEL_MODE in $ENV_FILE (run: xrandr on the panel). Touch mapping will still run."
    while IFS= read -r line; do
      logger -t meetingbox-kiosk "xrandr: $line"
    done < <(xrandr 2>/dev/null | head -25)
  fi
else
  logger -t meetingbox-kiosk "xrandr not installed; skipping mode/rotation (apt install x11-xserver-utils)"
fi

# Build 3x3 from env; log if MEETINGBOX_TOUCH_COORD_MATRIX is set but unparsable (e.g. missing quotes → only one number).
_touch_resolve_matrix() {
  local -n _out=$1
  _out=()
  if [[ -n "${MEETINGBOX_TOUCH_COORD_MATRIX:-}" ]]; then
    # Strip CR (Windows line endings in edited files) so read gets 9 fields.
    local raw="${MEETINGBOX_TOUCH_COORD_MATRIX//$'\r'/}"
    read -r -a _out <<< "$raw"
    if [[ ${#_out[@]} -ne 9 ]]; then
      logger -t meetingbox-kiosk "touch: MEETINGBOX_TOUCH_COORD_MATRIX must be exactly 9 numbers (got ${#_out[@]} fields). Use quotes: MEETINGBOX_TOUCH_COORD_MATRIX=\"0 1 0 -1 0 1 0 0 1\""
      _out=()
    fi
  elif [[ -n "${MEETINGBOX_TOUCH_MATRIX_PRESET:-}" ]]; then
    case "${MEETINGBOX_TOUCH_MATRIX_PRESET}" in
      right) _out=(0 1 0 -1 0 1 0 0 1) ;;   # 90° CW — try when panel uses --rotate right
      left) _out=(0 -1 1 1 0 0 0 0 1) ;;    # 90° CCW
      inverted) _out=(-1 0 1 0 -1 1 0 0 1) ;;
      normal) _out=(1 0 0 0 1 0 0 0 1) ;;
      # Swap touch X↔Y (no mirror). Use when taps land ~90° off (e.g. must tap left-mid to hit bottom-mid).
      swap_xy|swap_axes) _out=(0 1 0 1 0 0 0 0 1) ;;
      *)
        logger -t meetingbox-kiosk "touch: unknown MEETINGBOX_TOUCH_MATRIX_PRESET='${MEETINGBOX_TOUCH_MATRIX_PRESET}' (use right|left|inverted|normal|swap_xy)"
        ;;
    esac
  # map-to-output alone often leaves touch unrotated vs xrandr; match panel unless overridden above.
  elif [[ -n "${MEETINGBOX_PANEL_ROTATE:-}" ]]; then
    case "${MEETINGBOX_PANEL_ROTATE}" in
      right) _out=(0 1 0 -1 0 1 0 0 1) ;;
      left) _out=(0 -1 1 1 0 0 0 0 1) ;;
      inverted) _out=(-1 0 1 0 -1 1 0 0 1) ;;
      normal) _out=(1 0 0 0 1 0 0 0 1) ;;
    esac
  fi
}

# Apply libinput-style 3x3 matrix to a device (fixes offset when map-to-output is not enough).
_touch_apply_matrix() {
  local dev="$1"
  [[ -z "$dev" ]] && return 0
  local m=()
  _touch_resolve_matrix m
  if [[ ${#m[@]} -eq 9 ]]; then
    if xinput set-prop "$dev" "Coordinate Transformation Matrix" "${m[@]}" 2>/dev/null; then
      logger -t meetingbox-kiosk "xinput: Coordinate Transformation Matrix on '$dev': ${m[*]}"
    else
      logger -t meetingbox-kiosk "xinput: set Coordinate Transformation Matrix failed for '$dev' (run: xinput list-props '$dev' | grep -i Coordinate)"
    fi
  fi
}

_map_one() {
  local devname="$1"
  [[ -z "$devname" ]] && return 1
  local mapped=0
  if xinput map-to-output "$devname" "$OUT" 2>/dev/null; then
    logger -t meetingbox-kiosk "xinput: map-to-output '$devname' -> $OUT"
    mapped=1
  else
    logger -t meetingbox-kiosk "xinput: map-to-output '$devname' -> $OUT failed (applying matrix anyway if configured)"
  fi
  # Always try matrix when set — previously matrix only ran after a successful map, so a wrong OUT name meant matrix never applied.
  _touch_apply_matrix "$devname"
  [[ "$mapped" -eq 1 ]]
}

# Pointer slave by numeric id (avoids duplicate device names mapping the wrong slave).
_map_one_by_id() {
  local id="$1"
  [[ -z "$id" ]] && return 1
  local mapped=0
  if xinput map-to-output "$id" "$OUT" 2>/dev/null; then
    logger -t meetingbox-kiosk "xinput: map-to-output id=$id -> $OUT"
    mapped=1
  else
    logger -t meetingbox-kiosk "xinput: map-to-output id=$id -> $OUT failed (applying matrix anyway if configured)"
  fi
  _touch_apply_matrix "$id"
  [[ "$mapped" -eq 1 ]]
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

# Run touch alignment whenever enabled — not only when xrandr succeeded (xrandr can fail while X + touch still need calibration).
if [[ "${MEETINGBOX_MAP_TOUCH_TO_OUTPUT:-1}" == "1" ]]; then
  _map_touch_to_output
fi

exit 0
