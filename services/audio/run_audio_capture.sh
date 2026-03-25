#!/usr/bin/env bash
# Run MeetingBox audio capture on the HOST (recommended — direct ALSA/Pulse access).
#
# Docker: the `audio` service is behind profile `docker-audio` only; default
# `docker compose up` does not start audio in a container.
#
# One-time setup (Debian/Ubuntu):
#   sudo apt install -y portaudio19-dev libasound2-dev python3-dev build-essential
#   cd services/audio && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
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
PYTHON_CMD="${PYTHON:-python3}"

if [[ "${MEETINGBOX_USE_VENV:-1}" != "0" ]] && [[ -f "$ACTIVATE" ]]; then
  # shellcheck source=/dev/null
  source "$ACTIVATE"
  PYTHON_CMD="python3"
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

mkdir -p "$TEMP_SEGMENTS_DIR" "$RECORDINGS_DIR"

echo "[MeetingBox audio] REDIS_HOST=$REDIS_HOST" >&2
echo "[MeetingBox audio] TEMP_SEGMENTS_DIR=$TEMP_SEGMENTS_DIR" >&2
echo "[MeetingBox audio] RECORDINGS_DIR=$RECORDINGS_DIR" >&2
echo "[MeetingBox audio] PYTHON=$("$PYTHON_CMD" -c 'import sys; print(sys.executable)')" >&2

exec "$PYTHON_CMD" audio_capture.py "$@"
