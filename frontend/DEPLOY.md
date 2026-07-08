# Production deploy — avoiding 403 Forbidden

## Serve the **`dist/`** folder, not the repo root

Nginx (or Docker `FRONTEND_DIST`) must point at the **contents of `dist/`**, not the git checkout root.

| Correct | Wrong (often returns **403 Forbidden**) |
|---------|----------------------------------------|
| `root /home/ubuntu/meetingbox-frontend-release/dist;` | `root /home/ubuntu/meetingbox-frontend-release;` |
| `FRONTEND_DIST=../meetingbox-frontend-release/dist` | `FRONTEND_DIST=../meetingbox-frontend-release` |

After `npm run build`, deploy **all** of `dist/` (`index.html`, `assets/*`, `icons/*`). Committing or rsyncing only `dist/index.html` leaves missing JS bundles and breaks the app.

## Build on the server

```bash
cd ~/meetingbox-frontend-release
git checkout -- dist/    # discard stale local dist before pull
git pull
cp .env.production.example .env.production   # first time only; edit if split hosting
npm ci
npm run build          # verify-dist + open-dist-perms (fixes unreadable files → 403)
```

Point the server stack at the built folder (sibling clone example):

```env
FRONTEND_DIST=/home/ubuntu/meetingbox-frontend-release/dist
```

Restart nginx/docker after build so the volume picks up new files:

```bash
cd ~/meetingbox-server   # or your server repo path
docker compose restart nginx
```

### Quick test (static root vs API)

```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1/deploy-check.txt
# expect 200 and body: meetingbox-static-ok

curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1/
# expect 200 (HTML)

curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1/health
# expect 200 from API
```

If `deploy-check.txt` returns **403** but nginx config is correct, `dist/` is empty, unreadable, or **FRONTEND_DIST** points at the wrong directory. The build log prints the exact path to use.

If `/` returns **403** while `dist/index.html` exists, make sure your nginx SPA fallback does **not** include `$uri/`. `try_files $uri $uri/ /index.html` lets `/` match the root directory and nginx forbids directory listing.

## `.env.production` and 403 on API calls

- **One host** (nginx serves SPA and proxies `/api` to FastAPI): leave `VITE_API_URL` **unset** in `.env.production`.
- **Split hosting** (SPA on S3/CloudFront, API on another domain): set `VITE_API_URL` to the **API origin only**, never the CDN/SPA URL. Using the static site URL makes `/api/*` hit the bucket → **403 Forbidden**.

## SPA routing (deep links)

The reverse proxy must fall back to `index.html` for client routes (`/dashboard`, `/emails`, …).

Example (same as `server/nginx/nginx.conf`):

```nginx
root /var/www/meetingbox/dist;
location = / {
    try_files /index.html =404;
}
location = /favicon.ico {
    try_files /favicon.ico /icons/ic-logo.svg =204;
}
location / {
    try_files $uri /index.html;
}
location /api/ {
    proxy_pass http://127.0.0.1:8000;
}
```

## `meetingbox-frontend-release` git note

The monorepo ignores `dist/`. If your **release** repo tracks built files, do **not** add `/dist` to `.gitignore` there. If you only build on the server, ignore `dist/` in git and set `FRONTEND_DIST` to the local `dist/` path after each build.

## Subpath hosting (optional)

If the app is not at the domain root:

```env
VITE_BASE_PATH=/meetingbox/
```

Rebuild; nginx `location` must match that prefix.
