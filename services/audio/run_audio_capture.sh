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
#   REDIS_HOST=127.0.0.1 ./run_audio_capture.sh
#   AUDIO_INPUT_DEVICE_INDEX=2 ./run_audio_capture.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="${VENV_DIR:-.venv}"
ACTIVATE="$VENV_DIR/bin/activate"

if [[ ! -f "$ACTIVATE" ]]; then
  echo "Missing venv: $SCRIPT_DIR/$VENV_DIR" >&2
  echo "Create it with: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$ACTIVATE"

# webrtcvad imports pkg_resources — that module ships with setuptools, not PyPI "pkg_resources".
if ! python3 -c "import pkg_resources" 2>/dev/null; then
  echo "[MeetingBox audio] Installing setuptools (required by webrtcvad). Run: pip install -r requirements.txt" >&2
  pip install "setuptools>=69.0.0"
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

exec python3 audio_capture.py "$@"
