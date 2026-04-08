#!/usr/bin/env bash
# Run MeetingBox device UI from a local venv (Linux / mini PC).
#
# One-time setup:
#   cd mini-pc/device-ui && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
#
# Usage:
#   ./run_device_ui.sh
#   MOCK_BACKEND=1 ./run_device_ui.sh
#   DISPLAY=:0 ./run_device_ui.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
MINI_PC_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# When this tree lives under the full monorepo: mini-pc/../docker-compose.server.yml
MONOREPO_ROOT=""
if [[ -f "$MINI_PC_ROOT/../docker-compose.server.yml" ]]; then
  MONOREPO_ROOT="$(cd "$MINI_PC_ROOT/.." && pwd)"
fi

VENV_DIR="${VENV_DIR:-.venv}"
ACTIVATE="$VENV_DIR/bin/activate"

if [[ ! -f "$ACTIVATE" ]]; then
  echo "Missing venv: $SCRIPT_DIR/$VENV_DIR" >&2
  echo "Create it with: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

_load_env_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  set -a
  # shellcheck source=/dev/null
  source /dev/stdin <<<"$(tr -d '\r' < "$f")"
  set +a
}

# shellcheck source=/dev/null
source "$ACTIVATE"

# Order: monorepo shared secrets → appliance package → per-app overrides
[[ -n "$MONOREPO_ROOT" ]] && _load_env_file "$MONOREPO_ROOT/.env"
_load_env_file "$MINI_PC_ROOT/.env"
_load_env_file "$SCRIPT_DIR/.env"

# Server compose uses APP_BASE_URL; device UI expects BACKEND_URL.
if [[ -z "${BACKEND_URL:-}" ]] && [[ -n "${APP_BASE_URL:-}" ]]; then
  export BACKEND_URL="${APP_BASE_URL%/}"
fi

export DISPLAY_WIDTH="${DISPLAY_WIDTH:-1024}"
export DISPLAY_HEIGHT="${DISPLAY_HEIGHT:-600}"

# If you launched this from SSH, DISPLAY is often localhost:10.0 (forwarded X).
# MoTTY / missing xauth then shows no window on the real monitor. Prefer local :0
# when the console X socket exists. Opt out: MEETINGBOX_KEEP_SSH_X=1
if [[ "$(uname -s)" == "Linux" ]] && [[ "${MEETINGBOX_KEEP_SSH_X:-0}" != "1" ]]; then
  if [[ -n "${DISPLAY:-}" && "${DISPLAY}" == localhost:* ]] && [[ -S /tmp/.X11-unix/X0 ]]; then
    echo "[MeetingBox] DISPLAY was ${DISPLAY}; switching to :0 for local monitor (set MEETINGBOX_KEEP_SSH_X=1 to keep SSH X11)." >&2
    export DISPLAY=:0
  fi
fi
# Explicit override: MEETINGBOX_USE_LOCAL_X=1 forces :0 when available.
if [[ "$(uname -s)" == "Linux" ]] && [[ "${MEETINGBOX_USE_LOCAL_X:-0}" == "1" ]] && [[ -S /tmp/.X11-unix/X0 ]]; then
  export DISPLAY=:0
fi

# Console/kiosk: DISPLAY often unset when launching from systemd or tty; SDL
# then may not map a visible window. Use local :0 when the socket exists.
if [[ "$(uname -s)" == "Linux" ]] && [[ -z "${DISPLAY:-}" ]] && [[ -S /tmp/.X11-unix/X0 ]]; then
  export DISPLAY=:0
  echo "[MeetingBox] DISPLAY was unset; set to :0 for local X11 (socket X0 present)." >&2
fi

# Kivy on Linux: SDL2 clipboard still loads an X11 "cutbuffer" helper; without
# xclip/xsel it logs CRITICAL (see kivy/core/clipboard/__init__.py).
if [[ "$(uname -s)" == "Linux" ]] && ! command -v xclip >/dev/null 2>&1 && ! command -v xsel >/dev/null 2>&1; then
  echo "[MeetingBox] Tip: install xclip to silence Kivy Cutbuffer errors: sudo apt install xclip" >&2
fi

# GUI needs a real local display for kiosk hardware (not broken SSH X11).
if [[ "$(uname -s)" == "Linux" ]] && [[ -z "${DISPLAY:-}" ]]; then
  echo "[MeetingBox] DISPLAY is not set. On the device console try: export DISPLAY=:0" >&2
fi

if [[ -z "${BACKEND_URL:-}" ]] && [[ "${MOCK_BACKEND:-0}" != "1" ]]; then
  echo "[MeetingBox] BACKEND_URL is not set — the UI will use http://localhost:8000 (no server on this machine)." >&2
  echo "[MeetingBox] Fix: set BACKEND_URL in $MINI_PC_ROOT/.env (see .env.example), monorepo .env, or $SCRIPT_DIR/.env" >&2
fi

exec python3 src/main.py "$@"
