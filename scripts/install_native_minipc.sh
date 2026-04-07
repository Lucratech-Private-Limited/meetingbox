#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Native mini-PC installer with Ollama + local Whisper.cpp has been removed.
# The product stack is: Docker (redis + web + nginx), OpenAI transcription,
# Anthropic summarization — plus host audio and device-ui on the appliance.
# ---------------------------------------------------------------------------

if [ "${EUID}" -ne 0 ]; then
  echo "This helper is informational. Run: cat scripts/install_native_minipc.sh"
  exit 1
fi

echo "============================================"
echo " MeetingBox — use Docker on the mini PC"
echo "============================================"
echo ""
echo "The previous native install (Redis + Ollama + whisper.cpp + systemd"
echo "transcription/ai workers) is no longer maintained."
echo ""
echo "Install Docker, clone this repo to e.g. /home/meetingbox/meetingbox, then:"
echo ""
echo "  cd /home/meetingbox/meetingbox"
echo "  cp .env.example .env"
echo "  # Set JWT_SECRET_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, etc."
echo "  cd frontend && npm ci && npm run build && cd .."
echo "  docker compose up -d --build"
echo ""
echo "See also: scripts/install_device_ui.sh (device-focused Docker install)"
echo "          deploy/README-SERVER.md (cloud API server)"
echo ""
exit 2
