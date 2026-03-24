#!/usr/bin/env bash
# Run MeetingBox device UI from a local venv (Linux / mini PC).
#
# One-time setup:
#   cd device-ui && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
#
# Usage:
#   ./run_device_ui.sh
#   MOCK_BACKEND=1 ./run_device_ui.sh
#   DISPLAY=:0 ./run_device_ui.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="${VENV_DIR:-.venv}"
ACTIVATE="$VENV_DIR/bin/activate"

if [[ ! -f "$ACTIVATE" ]]; then
  echo "Missing venv: $SCRIPT_DIR/$VENV_DIR" >&2
  echo "Create it with: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$ACTIVATE"

export DISPLAY_WIDTH="${DISPLAY_WIDTH:-1024}"
export DISPLAY_HEIGHT="${DISPLAY_HEIGHT:-600}"

exec python3 src/main.py "$@"
