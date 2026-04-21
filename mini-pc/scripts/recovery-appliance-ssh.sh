#!/usr/bin/env bash
# Run over SSH when the panel shows Ubuntu but no app / docker ps is empty.
# Does not fix GDM; it gets Redis, audio, and (if possible) the UI stack running again.

set -euo pipefail

echo "=== Docker daemon ==="
sudo systemctl enable --now docker

REL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REL"

if [[ ! -f docker-compose.yml ]]; then
  echo "No docker-compose.yml in $REL" >&2
  exit 1
fi

echo "=== Redis + audio (works without X11) ==="
docker compose --profile docker-audio up -d redis audio

echo "=== device-ui + rest (needs working X / cookie for UI) ==="
docker compose --profile mini-pc --profile docker-audio up -d || true

echo "=== Containers ==="
docker ps -a

echo ""
echo "If device-ui keeps restarting: fix X11 (see README) or log in on the panel once, then:"
echo "  bash scripts/kiosk-compose-up.sh $REL"
