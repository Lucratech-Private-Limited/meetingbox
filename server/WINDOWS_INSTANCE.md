# Windows server instance (`window_mac_port`)

This branch adds a **second, independent server instance** for the Windows exe
version of the device UI. It runs the **same application code** as the main
stack but as a **separate Docker container on its own port**, and it **shares
the main stack's data** so both clients see the same users, meetings and
integrations.

> Only this branch (`window_mac_port`) contains the Windows instance. `main`
> is unchanged, so the mini-PC deployment keeps running exactly as before.

## What runs where

| Instance | Compose file | Container | Host port | Serves |
|----------|--------------|-----------|-----------|--------|
| Mini-PC (existing) | `docker-compose.yml` | `meetingbox-web` (+ nginx) | 80 / 8000 | mini-PC device UI + dashboard |
| Windows (new) | `docker-compose.windows.yml` | `meetingbox-web-win` | `${WINDOWS_WEB_PORT:-8100}` | Windows exe device UI |

Both `web` containers:
- join the **same Docker network** (so the Windows one reaches `redis`,
  `mem0-postgres`, `mem0-neo4j`),
- mount the **same `DATA_ROOT`** (one shared SQLite DB, audio, config),
- publish/subscribe on the **same Redis** event bus.

```
Windows exe UI ──:8100──▶ meetingbox-web-win ─┐
                                              ├─▶ redis / postgres / neo4j
Mini-PC UI    ──:8000──▶ meetingbox-web ──────┘        + shared DATA_ROOT
```

## Deploy

From the server repo root (this folder):

```bash
# 0) one-time: configure secrets/paths in .env (same file the main stack uses)
cp .env.example .env            # if you don't have one yet
#   append the Windows knobs from .env.windows.example (WINDOWS_WEB_PORT, etc.)

# 1) start the MAIN stack first — it creates the shared network + redis/postgres/neo4j
docker compose up -d

# 2) start the Windows instance, sharing that network + data
docker compose -f docker-compose.windows.yml --env-file .env up -d
```

Order matters: the Windows instance joins the main stack's network as
`external`, so the main stack must be up first. Stop it with:

```bash
docker compose -f docker-compose.windows.yml down
```

## Connect the Windows device UI

Point the Windows exe at the new port on the server host:

- API base: `http://<server-host>:8100`
- WebSocket: `ws://<server-host>:8100/ws`

(Use `https`/`wss` if you front the port with TLS.)

## Verify

```bash
# health
curl http://localhost:8100/health          # -> {"status":"healthy",...}

# confirm the shared network name matches SHARED_NETWORK_NAME in .env
docker network ls | grep meetingbox

# validate the compose file resolves
docker compose -f docker-compose.windows.yml --env-file .env config >/dev/null && echo OK
```

If `docker network ls` shows a different network name than
`meetingbox-server_meetingbox-server-net`, set `SHARED_NETWORK_NAME` in `.env`
to the actual name.

## Notes / caveats

- **Shared SQLite:** both instances write to the same `meetings.db`. The app
  already runs multi-worker SQLite, so low/medium load is fine (WAL). If you see
  `database is locked` under heavy concurrent writes, migrate the DB to Postgres.
- **Shared Redis is intentional:** recording/summary events broadcast to both
  the mini-PC and Windows clients.
- **OAuth:** the Windows instance reuses the main `APP_BASE_URL` /
  `OAUTH_PUBLIC_BASE_URL` (integrations are connected once via the shared
  dashboard). If the Windows app must start its own OAuth flow, add its URL to
  the Google redirect URIs.
- **Adding Windows-only endpoints later:** add them on this branch. The
  `MEETINGBOX_INSTANCE=windows` env var lets code branch per instance without
  touching `main`. Periodically merge `main` into `window_mac_port` to stay in
  sync with shared code.
