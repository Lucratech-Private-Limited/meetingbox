#!/usr/bin/env bash
# Wait for local X11 + a usable MIT cookie, allow Docker to connect, then docker compose up -d.
# Used by systemd meetingbox-appliance.service (do not run with sudo — the unit runs as the GUI user).
#
# Copies the working cookie to APPLIANCE_DIR/.meetingbox-docker.xauth and passes that path as
# XAUTHORITY_HOST for this invocation so the device-ui bind mount matches GDM/Ubuntu 24 setups.

set -euo pipefail

APPLIANCE_DIR="${1:-${APPLIANCE_DIR:-$HOME/meetingbox-mini-pc-release}}"
APPLIANCE_DIR=$(cd "$APPLIANCE_DIR" && pwd)
COMPOSE_FILE="$APPLIANCE_DIR/docker-compose.yml"
XAUTH_COPY="$APPLIANCE_DIR/.meetingbox-docker.xauth"
u_id="$(id -u)"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "kiosk-compose-up: no docker-compose.yml in $APPLIANCE_DIR" >&2
  exit 1
fi

wait_for_x_socket() {
  local max="${1:-120}"
  local i
  for ((i = 1; i <= max; i++)); do
    if [[ -S /tmp/.X11-unix/X0 ]]; then
      echo ":0"
      return 0
    fi
    if [[ -S /tmp/.X11-unix/X1 ]]; then
      echo ":1"
      return 0
    fi
    sleep 1
  done
  return 1
}

pick_cookie_source() {
  local gdm="/run/user/${u_id}/gdm/Xauthority"
  local rt="${XDG_RUNTIME_DIR:-/run/user/${u_id}}"
  local f
  # GDM (Xorg)
  if [[ -f "$gdm" && -s "$gdm" ]]; then
    echo "$gdm"
    return 0
  fi
  # GNOME on Wayland: mutter leaves Xwayland cookies here
  for f in "${rt}"/.mutter-Xwaylandauth.*; do
    if [[ -f "$f" && -s "$f" ]]; then
      echo "$f"
      return 0
    fi
  done
  if [[ -f "$HOME/.Xauthority" && -s "$HOME/.Xauthority" ]]; then
    echo "$HOME/.Xauthority"
    return 0
  fi
  return 1
}

echo "kiosk-compose-up: waiting for X11 socket (max ~120s)..."
if ! disp_num=$(wait_for_x_socket 120); then
  echo "kiosk-compose-up: timed out waiting for /tmp/.X11-unix/X0 or X1" >&2
  exit 1
fi

echo "kiosk-compose-up: waiting for Xauthority cookie..."
src=""
for _try in $(seq 1 60); do
  if src=$(pick_cookie_source); then
    break
  fi
  sleep 2
done
if [[ -z "${src:-}" ]]; then
  echo "kiosk-compose-up: no usable cookie in /run/user/${u_id}/gdm/Xauthority or ~/.Xauthority" >&2
  echo "kiosk-compose-up: enable auto-login for this user or log in once on the built-in screen, then reboot." >&2
  exit 1
fi

export DISPLAY="$disp_num"
export XAUTHORITY="$src"
if /usr/bin/xhost "+local:docker" 2>/dev/null; then
  echo "kiosk-compose-up: xhost +local:docker ok"
else
  echo "kiosk-compose-up: xhost failed (continuing — cookie copy may still be enough)" >&2
fi

if [[ -d "$XAUTH_COPY" ]]; then
  echo "kiosk-compose-up: removing bogus directory $XAUTH_COPY (usually a bad Docker bind when the cookie file was missing)" >&2
  rm -rf "$XAUTH_COPY"
fi
cp "$src" "$XAUTH_COPY"
chmod 600 "$XAUTH_COPY"

echo "kiosk-compose-up: cookie source=$src size=$(wc -c <"$XAUTH_COPY") bytes -> $XAUTH_COPY"
if command -v xauth >/dev/null 2>&1; then
  xauth -f "$XAUTH_COPY" list 2>/dev/null | head -5 >&2 || true
fi

cd "$APPLIANCE_DIR"
# MEETINGBOX_X11_COOKIE is NOT in .env — Compose always uses this file for the bind mount on boot.
export MEETINGBOX_X11_COOKIE="$XAUTH_COPY"
export DEVICE_UI_DISPLAY="$disp_num"
# Services are gated by Compose profiles; if .env omits COMPOSE_PROFILES, plain ``up -d``
# starts no device-ui/redis/audio. Default only when .env does not define the variable.
if [[ ! -f "$APPLIANCE_DIR/.env" ]] || ! grep -qE '^[[:space:]]*COMPOSE_PROFILES=' "$APPLIANCE_DIR/.env"; then
  export COMPOSE_PROFILES="${COMPOSE_PROFILES:-mini-pc,docker-audio}"
fi
# One shot only — do not ``--force-recreate device-ui`` here; that stops the UI right after
# the first start and looks like “fullscreen then closes and opens again” on the panel.
/usr/bin/docker compose up -d
