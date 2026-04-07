# Web stack (AWS / cloud server)

**Prefer** running from the repo root after `git pull`:

`docker compose -f docker-compose.server.yml up -d --build`

This folder is an alternate copy of the same three services; paths here use `../` instead of `./`.

Runs **Redis + FastAPI + nginx** from the monorepo. Use this on a server while:

| Piece | Where |
|--------|--------|
| This compose | EC2 / ECS / VPS |
| `frontend/dist` | S3 + CloudFront (build with `VITE_API_URL`) |
| Device UI | Mini PC (`BACKEND_URL` → your API) |
| Host audio | Mini PC (`REDIS_HOST`, `UPLOAD_AUDIO_API_URL`) |

## 1. Server (this folder)

```bash
cp .env.example .env
# Edit .env: JWT_SECRET_KEY, APP_BASE_URL (https API), FRONTEND_BASE_URL (S3/CloudFront), API keys

# Build SPA with API URL, then start stack
cd ../frontend
cp .env.production.example .env.production
# Edit .env.production: VITE_API_URL=https://api.example.com
npm ci && npm run build
cd ../web-stack

docker compose up -d --build
```

Put TLS in front (ALB, Caddy, etc.); set `APP_BASE_URL` / `FRONTEND_BASE_URL` to the **public** https URLs.

### Redis from mini PC

Audio publishes to the **same Redis** as `web`. Prefer **Site-to-Site VPN / WireGuard** and a private Redis endpoint. Opening `6379` publicly is risky; if you must, lock the security group to the mini PC IP and set `REDIS_PORT_MAPPING=0.0.0.0:6379:6379`.

## 2. Mini PC — audio

See `../deploy/mini-pc-aws-backend.env.example`. Export variables before running `services/audio/run_audio_capture.sh` (or your systemd unit).

## 3. Mini PC — device UI

Set:

- `BACKEND_URL=https://api.example.com`
- `BACKEND_WS_URL` optional (defaults from `BACKEND_URL`)

See `../deploy/mini-pc-aws-backend.env.example`.

## Monorepo paths

Compose expects `../services/web`, `../data`, `../frontend/dist`, `../scripts` relative to this directory.
