# Server deploy (`git pull` on AWS / VPS)

Run only the **API stack** from the repository root with:

```bash
docker compose -f docker-compose.server.yml up -d --build
```

## What this starts

| Service   | Role |
|----------|------|
| `redis`  | Event bus; mini PC **host audio** must connect to this same Redis |
| `web`    | FastAPI (`services/web`) |
| `nginx`  | Serves `frontend/dist` and proxies `/api`, `/ws` to `web` |

Not started: **device-ui**, **Docker audio** — those belong on the mini PC (or use host audio).

## One-time setup

1. **Clone / pull** the repo on the server.

2. **Environment** (repo root):

   ```bash
   cp .env.server.example .env
   ```

   Set at least `JWT_SECRET_KEY`, `APP_BASE_URL` (public API URL), `FRONTEND_BASE_URL` (S3/CloudFront SPA URL if you use OAuth there), and any API keys.

3. **Build the SPA** (nginx serves `frontend/dist/`):

   ```bash
   cd frontend
   cp .env.production.example .env.production
   # Set VITE_API_URL=https://your-api-host  (no trailing slash)
   npm ci && npm run build
   cd ..
   ```

4. **Data directories** — ensure `data/transcripts`, `data/audio`, `data/config` exist (compose mounts them from the repo).

5. **Start**:

   ```bash
   docker compose -f docker-compose.server.yml up -d --build
   ```

6. **TLS** — put **ACM + ALB**, **Caddy**, or another proxy in front; update `APP_BASE_URL` / `FRONTEND_BASE_URL` to **https**.

## Mini PC

Host audio + device UI: see `mini-pc-aws-backend.env.example` — `REDIS_HOST`, `REDIS_PORT`, `UPLOAD_AUDIO_API_URL`, `BACKEND_URL`.

## Alternative path

The same stack also lives under `web-stack/` (paths relative to that folder). Prefer **`docker-compose.server.yml` at repo root** after `git pull` so you never `cd` into a subfolder.
