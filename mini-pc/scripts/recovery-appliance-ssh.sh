#!/usr/bin/env bash
# Run over SSH when the panel shows Ubuntu but no app / docker ps is empty.
# Does not fix GDM; it gets Redis and (if possible) the UI stack running again.
# Audio capture is supervised inside device-ui. Do not start the legacy
# docker-audio profile because it races device-ui for the microphone.

set -euo pipefail

echo "=== Docker daemon ==="
sudo systemctl enable --now docker

REL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REL"

if [[ ! -f docker-compose.yml ]]; then
  echo "No docker-compose.yml in $REL" >&2
  exit 1
fi

echo "=== device-ui + Redis (needs working X / cookie for UI) ==="
COMPOSE_PROFILES=mini-pc docker compose up -d --remove-orphans || true

echo "=== Containers ==="
docker ps -a

echo ""
echo "If device-ui keeps restarting: fix X11 (see README) or log in on the panel once, then:"
echo "  bash scripts/kiosk-compose-up.sh $REL"
