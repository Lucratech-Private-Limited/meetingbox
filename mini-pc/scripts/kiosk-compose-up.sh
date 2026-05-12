#!/usr/bin/env bash
# Build/start device-ui with a valid :0 Xauthority cookie bind.
#
# Usage:
#   cd mini-pc
#   bash scripts/kiosk-compose-up.sh
#
# Optional env:
#   NO_BUILD=1                      # skip --build
#   DEVICE_UI_DISPLAY=:1            # override target display (default :0)
#   XAUTHORITY_HOST=/path/to/file   # preferred host cookie source

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "[MeetingBox] ERROR: docker not found." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "[MeetingBox] ERROR: docker compose v2 not available." >&2
  exit 1
fi
if ! command -v xauth >/dev/null 2>&1; then
  echo "[MeetingBox] ERROR: xauth not found. Install: sudo apt install xauth" >&2
  exit 1
fi

DISPLAY_TARGET="${DEVICE_UI_DISPLAY:-:0}"
export DEVICE_UI_DISPLAY="$DISPLAY_TARGET"
COOKIE_OUT="$ROOT_DIR/.meetingbox-docker.xauth"

_has_display_cookie() {
  local file="$1"
  local display="$2"
  [[ -f "$file" ]] || return 1
  xauth -f "$file" list "$display" 2>/dev/null | grep -Eq 'MIT-MAGIC-COOKIE' && return 0
  xauth -f "$file" list 2>/dev/null | grep -Eq '(^|[[:space:]])([^[:space:]]*:0([.][0-9]+)?|[^[:space:]]+/unix:0([.][0-9]+)?)([[:space:]]|$)' && return 0
  return 1
}

_candidate_cookie_sources() {
  local uid
  uid="$(id -u)"
  printf '%s\n' \
    "${XAUTHORITY_HOST:-}" \
    "${MEETINGBOX_X11_COOKIE:-}" \
    "$HOME/.meetingbox-docker.xauth" \
    "$HOME/.Xauthority" \
    "/run/user/$uid/gdm/Xauthority" \
    "/run/user/1000/gdm/Xauthority"
}

SOURCE_COOKIE=""
while IFS= read -r cand; do
  [[ -n "$cand" ]] || continue
  if _has_display_cookie "$cand" "$DISPLAY_TARGET"; then
    SOURCE_COOKIE="$cand"
    break
  fi
done < <(_candidate_cookie_sources)

if [[ -z "$SOURCE_COOKIE" ]]; then
  echo "[MeetingBox] ERROR: could not find a readable Xauthority file with cookie for $DISPLAY_TARGET." >&2
  echo "[MeetingBox] Fix once on the built-in screen:" >&2
  echo "  1) Log in locally as this user." >&2
  echo "  2) Run: echo \$DISPLAY; xauth list \$DISPLAY" >&2
  echo "  3) Re-run this script." >&2
  exit 1
fi

rm -f "$COOKIE_OUT"
touch "$COOKIE_OUT"
chmod 600 "$COOKIE_OUT"

# Merge only the target display cookie into a dedicated file for Docker bind.
if ! xauth -f "$SOURCE_COOKIE" nlist "$DISPLAY_TARGET" 2>/dev/null | sed -e 's/^..../ffff/' | xauth -f "$COOKIE_OUT" nmerge - >/dev/null 2>&1; then
  echo "[MeetingBox] ERROR: failed to extract cookie from $SOURCE_COOKIE" >&2
  exit 1
fi

if ! _has_display_cookie "$COOKIE_OUT" "$DISPLAY_TARGET"; then
  echo "[MeetingBox] ERROR: generated cookie file has no $DISPLAY_TARGET entry." >&2
  exit 1
fi

chmod 644 "$COOKIE_OUT"
export MEETINGBOX_X11_COOKIE="$COOKIE_OUT"

echo "[MeetingBox] Using DISPLAY=$DISPLAY_TARGET"
echo "[MeetingBox] Using X11 cookie: $SOURCE_COOKIE -> $COOKIE_OUT"

docker compose down
if [[ "${NO_BUILD:-0}" == "1" ]]; then
  docker compose up -d device-ui
else
  docker compose up -d --build device-ui
fi

echo "[MeetingBox] device-ui launch requested."
echo "[MeetingBox] Logs: docker logs -f meetingbox-appliance-ui"
