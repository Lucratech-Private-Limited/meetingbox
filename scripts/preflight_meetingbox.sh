#!/usr/bin/env bash
# Read-only checks before restarting MeetingBox (host + Docker split setups).
# Usage: bash scripts/preflight_meetingbox.sh [/path/to/repo]

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== MeetingBox preflight (repo: $ROOT) ==="
echo

warn() { echo "[WARN] $*" >&2; }
ok() { echo "[OK]   $*"; }

# --- Root .env (docker compose) ---
if [[ -f .env ]]; then
  if grep -qE '^[[:space:]]*UPLOAD_AUDIO_API_URL=http://web:8000' .env 2>/dev/null; then
    warn "Root .env sets UPLOAD_AUDIO_API_URL=http://web:8000 — only valid when meetingbox-web runs IN compose."
    warn "  For web-on-host + docker-audio: comment that line or use host.docker.internal (see docker-compose.yml)."
  else
    ok "Root .env: no hard-coded http://web:8000-only upload trap (or unset — compose default applies)."
  fi
  for key in ANTHROPIC_API_KEY OPENAI_API_KEY JWT_SECRET_KEY; do
    line="$(grep -E "^[[:space:]]*${key}=" .env 2>/dev/null | tail -1 || true)"
    val="${line#*=}"
    val="$(echo -n "$val" | tr -d ' \t\r')"
    if [[ -z "$line" || ${#val} -lt 8 ]]; then
      warn "Root .env: $key missing or very short (full Docker stack needs real keys)."
    else
      ok "Root .env: $key looks set."
    fi
  done
else
  warn "No root .env — create from .env.example if you use docker compose."
fi
echo

# --- server/web/.env (host uvicorn) ---
WEB_ENV="$ROOT/server/web/.env"
if [[ -f "$WEB_ENV" ]]; then
  if grep -qE '^RECORDINGS_DIR=/meetingbox/' "$WEB_ENV" 2>/dev/null; then
    warn "server/web/.env RECORDINGS_DIR under /meetingbox/ — usually not creatable by normal user; use repo data/ paths."
  fi
  if grep -qE '^REDIS_HOST=redis$' "$WEB_ENV" 2>/dev/null; then
    warn "server/web/.env REDIS_HOST=redis — wrong for host-only web unless you have a host alias named redis."
  fi
  if grep -qE '^REDIS_HOST=' "$WEB_ENV" 2>/dev/null; then
    ok "server/web/.env: REDIS_HOST set."
  else
    warn "server/web/.env: REDIS_HOST not set (default redis — use 127.0.0.1 on native host)."
  fi
else
  warn "No server/web/.env — host-run web may miss OPENAI/ANTHROPIC/REDIS/RECORDINGS_DIR."
fi
echo

# --- data dirs ---
for d in "$ROOT/data/audio/recordings" "$ROOT/data/audio/temp" "$ROOT/data/transcripts"; do
  if [[ -d "$d" ]]; then
    ok "Exists: $d"
  else
    warn "Missing (mkdir if you use host paths): $d"
  fi
done
echo

# --- Optional API probe ---
if command -v curl >/dev/null 2>&1; then
  if curl -sf "http://127.0.0.1:8000/api/system/device-info" >/dev/null 2>&1; then
    ok "GET http://127.0.0.1:8000/api/system/device-info — OK (web reachable)."
  else
    warn "Web not reachable on 127.0.0.1:8000 — start web or fix port."
  fi
else
  warn "curl not installed; skipping HTTP probe."
fi

echo
echo "=== Device / dashboard checklist (manual) ==="
echo "  - DEVICE_AUTH_TOKEN (mbd_...) in: docker audio env, device-ui env — same token after pairing."
echo "  - device-ui: BACKEND_URL / BACKEND_WS_URL must point at your web (default localhost:8000)."
echo "=== Done ==="
