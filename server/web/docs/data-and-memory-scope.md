# Data and memory scope (MeetingBox web)

This document describes where user-visible “memory” lives and which HTTP routes aggregate it for UIs.

## Stores

| Layer | Role | Persistence |
|-------|------|-------------|
| **SQLite** (`MEETINGBOX_DB_PATH`) | Meetings, segments, summaries, `user_commitments`, `actions`, `pending_assistant_actions`, users/devices | Durable on disk |
| **Redis** (`REDIS_HOST`) | Recording/events pub-sub; not end-user memory | Ephemeral / ops |
| **Mem0** (optional, `mem0ai`) | Vector recall per `user_id` when enabled | External service |
| **Google APIs** (OAuth per user) | Live Gmail + Calendar | Google |

## Client surfaces

- **React SPA** — uses JWT `Authorization: Bearer`. Consumes `/api/commitments`, `/api/briefing/context`, `/api/assistant/*`, etc.
- **Kivy device-ui** — uses paired device token (`mbd_…`) with the same Bearer header on most routes. Uses `/api/briefing/context`, `/api/calendar/week`, `/api/commitments`, `/api/device/*`.

## Key HTTP contracts

- **`GET /api/briefing/context`** — Single bundle: local date range calendar (`days`), SQLite commitments, recent meetings (DB), optional Mem0 snippet, pending assistant rows, optional Gmail preview. Auth: `get_current_actor` (user JWT or device token).
- **`GET /api/calendar/week?start=&end=`** — Google Calendar events grouped as `{ "days": { "YYYY-MM-DD": { "meetings": [...] } } }` (same shape as device `get_calendar_week`). Auth: actor.
- **`GET /api/commitments`** — Lists `user_commitments` for the actor’s owner user. Auth: actor (device or user).

## Environment notes

- **`MEETINGBOX_MEM0_DISABLE`** — Turns off Mem0 search/ingest paths.
- **`MEETINGBOX_GMAIL_LIST_DEFAULT_Q`** — Overrides the default Gmail search fragment when `q` is empty for `list_recent_messages` (Primary + invite heuristics by default). When the user/assistant passes a non-empty `q`, it is **AND‑merged** with this scope unless it targets another mailbox (`in:spam`, `in:sent`, etc.).
- **`MEETINGBOX_GMAIL_POSTFILTER=0`** — Disables server-side hiding of rows that look like security/billing/subscription blasts (noise Subject + transactional From), after Gmail returns results.

## Related code

- Briefing routes: `routes/briefing.py`
- Calendar helpers: `services/calendar.py`
- Gmail listing: `services/gmail.py`
- Commitments: `services/commitments_service.py`, `routes/commitments.py`
