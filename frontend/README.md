# MeetingBox frontend (React / Vite)

SPA for the MeetingBox dashboard. Built assets (`dist/`) are served by the FastAPI **`web`** container and **nginx** in the **server** stack (sibling `server/` folder in the monorepo, or your `meetingbox-server` repository).

## Quick start (development)

```bash
cd frontend
npm ci
npm run dev
```

Default dev server: Vite reads API URLs from env (see `src/config` / `.env` local overrides).

## Production build

```bash
cp .env.production.example .env.production
# Set VITE_API_URL (and optional VITE_WS_URL) to your public API origin.
npm ci
npm run build
```

Output: `dist/` (verified by `scripts/verify-dist.mjs` after each build). The server stack mounts **that directory** — see `DEPLOY.md` if you see **403 Forbidden** after deploy.

## WebSocket (live recording / captions)

`useWebSocket` connects to `VITE_WS_URL` or same-origin `/ws`. When the user is logged in, **`access_token`** is appended from `localStorage` (`auth_token`) so the API can enforce **`MEETINGBOX_WS_REQUIRE_AUTH`**. If the API uses **only** `MEETINGBOX_WS_SHARED_SECRET`, set `VITE_WS_URL` to include `?token=...` (or protect `/ws` at the reverse proxy).

## Splitting into its own git repository

From the **monorepo root** (preserves history for this subtree):

```bash
git fetch origin   # ensure refs are current
git subtree split --prefix=frontend -b frontend-release
git push <your-frontend-remote> frontend-release:main
```

Clone that repo elsewhere — it has `package.json` at the root. CI can run `npm ci && npm run build` and publish `dist/` (artifact upload, rsync to VPS, etc.).

## Working with a **separate server** repo

On the VPS or build host, either:

1. **Sibling checkouts** — clone server and frontend next to each other, then in `server/.env`:
   ```env
   FRONTEND_DIST=../meetingbox-frontend/dist
   DATA_ROOT=./data
   ```
   (adjust the relative path to match your directory names), or  

2. **Copy artifacts** — build in the frontend repo and copy `dist/` into the server repo (e.g. `server/static/dist`) and set `FRONTEND_DIST` to that path.

The server `docker-compose.yml` bind-mounts `FRONTEND_DIST` into nginx and the `web` container; no Node is required on the server at runtime.
