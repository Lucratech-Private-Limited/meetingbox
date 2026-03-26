#!/usr/bin/env bash
# Run MeetingBox audio capture on the HOST (recommended — direct ALSA/Pulse access).
#
# Docker: the `audio` service is behind profile `docker-audio` only; default
# `docker compose up` does not start audio in a container.
#
# One-time setup (Debian/Ubuntu):
#   sudo apt install -y portaudio19-dev libasound2-dev python3-dev build-essential python3-venv
#   cd services/audio && python3 -m venv .venv
#   .venv/bin/python3 -m pip install -U pip setuptools
#   .venv/bin/python3 -m pip install -r requirements.txt
#
# If you see "No module named 'pkg_resources'" (webrtcvad needs setuptools):
#   cd services/audio && .venv/bin/python3 -m pip install -U "setuptools>=69"
#
# Prerequisites:
#   - Redis reachable from the host. Compose publishes Redis on 127.0.0.1:6379.
#   - Stack up: docker compose up -d   (redis, web, transcription, …)
#
# Usage:
#   ./run_audio_capture.sh
#   MEETINGBOX_USE_VENV=0 ./run_audio_capture.sh
#   PYTHON=/usr/bin/python3.12 ./run_audio_capture.sh
#   REDIS_HOST=127.0.0.1 ./run_audio_capture.sh
#   AUDIO_INPUT_DEVICE_INDEX=2 ./run_audio_capture.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="${VENV_DIR:-.venv}"
ACTIVATE="$VENV_DIR/bin/activate"
# Use an absolute interpreter for non-venv mode so a previously-activated
# venv in PATH cannot hijack execution.
if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_CMD="$PYTHON"
elif [[ "${MEETINGBOX_USE_VENV:-1}" == "0" ]] && [[ -x "/usr/bin/python3" ]]; then
  PYTHON_CMD="/usr/bin/python3"
else
  PYTHON_CMD="python3"
fi

if [[ "${MEETINGBOX_USE_VENV:-1}" != "0" ]] && [[ -f "$ACTIVATE" ]]; then
  # shellcheck source=/dev/null
  source "$ACTIVATE"
  # Absolute venv python — avoids a stale/wrong `python3` on PATH (e.g. another venv).
  PYTHON_CMD="$SCRIPT_DIR/$VENV_DIR/bin/python3"
  echo "[MeetingBox audio] Using venv: $SCRIPT_DIR/$VENV_DIR" >&2
elif [[ "${MEETINGBOX_USE_VENV:-1}" != "0" ]] && [[ ! -f "$ACTIVATE" ]]; then
  echo "[MeetingBox audio] No venv at $SCRIPT_DIR/$VENV_DIR — using system $PYTHON_CMD" >&2
  echo "[MeetingBox audio] Tip: python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt" >&2
else
  echo "[MeetingBox audio] MEETINGBOX_USE_VENV=0 — using system $PYTHON_CMD" >&2
fi

# webrtcvad imports pkg_resources — that module ships with setuptools.
if ! "$PYTHON_CMD" -c "import pkg_resources" >/dev/null 2>&1; then
  echo "[MeetingBox audio] pkg_resources missing; installing setuptools..." >&2
  if ! "$PYTHON_CMD" -m pip --version >/dev/null 2>&1; then
    "$PYTHON_CMD" -m ensurepip --upgrade >/dev/null 2>&1 || true
  fi
  "$PYTHON_CMD" -m pip install --upgrade "setuptools>=69.0.0"
fi

if ! "$PYTHON_CMD" -c "import pkg_resources" >/dev/null 2>&1; then
  echo "[MeetingBox audio] ERROR: pkg_resources still missing." >&2
  echo "[MeetingBox audio] Try: $PYTHON_CMD -m pip install -r requirements.txt" >&2
  exit 1
fi

export REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
export TEMP_SEGMENTS_DIR="${TEMP_SEGMENTS_DIR:-$REPO_ROOT/data/audio/temp}"
export RECORDINGS_DIR="${RECORDINGS_DIR:-$REPO_ROOT/data/audio/recordings}"
export AUDIO_INPUT_DEVICE_INDEX="${AUDIO_INPUT_DEVICE_INDEX:-}"
export AUDIO_INPUT_DEVICE_NAME="${AUDIO_INPUT_DEVICE_NAME:-}"
export UPLOAD_AUDIO_ON_STOP="${UPLOAD_AUDIO_ON_STOP:-1}"
export UPLOAD_AUDIO_API_URL="${UPLOAD_AUDIO_API_URL:-http://127.0.0.1:8000/api/meetings/upload-audio}"
export UPLOAD_AUDIO_TIMEOUT_SECONDS="${UPLOAD_AUDIO_TIMEOUT_SECONDS:-1200}"

mkdir -p "$TEMP_SEGMENTS_DIR" "$RECORDINGS_DIR"

echo "[MeetingBox audio] REDIS_HOST=$REDIS_HOST" >&2
echo "[MeetingBox audio] TEMP_SEGMENTS_DIR=$TEMP_SEGMENTS_DIR" >&2
echo "[MeetingBox audio] RECORDINGS_DIR=$RECORDINGS_DIR" >&2
echo "[MeetingBox audio] UPLOAD_AUDIO_ON_STOP=$UPLOAD_AUDIO_ON_STOP" >&2
echo "[MeetingBox audio] UPLOAD_AUDIO_API_URL=$UPLOAD_AUDIO_API_URL" >&2
echo "[MeetingBox audio] UPLOAD_AUDIO_TIMEOUT_SECONDS=$UPLOAD_AUDIO_TIMEOUT_SECONDS" >&2
echo "[MeetingBox audio] PYTHON=$("$PYTHON_CMD" -c 'import sys; print(sys.executable)')" >&2

exec "$PYTHON_CMD" audio_capture.py "$@"
