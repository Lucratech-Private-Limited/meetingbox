# MeetingBox Frontend Reference

> Single source of truth for every UI interaction, the API it calls, and the backend route it hits.
> **Update this file whenever a significant frontend or backend change is made.**

---

## Architecture Overview

- **Framework**: React 18 + TypeScript + Vite (port 3000 dev)
- **State**: Zustand (3 stores: `authStore`, `meetingStore`, `actionStore`)
- **HTTP**: Axios client at `frontend/src/api/client.ts` (base URL: relative, proxied to `http://localhost:8000` in dev)
- **WebSocket**: `ws://${window.location.host}/ws` via `useWebSocket` hook
- **Styling**: Tailwind CSS + Headless UI
- **Routing**: React Router v6 (`BrowserRouter`)

### Key Directories

| Directory | Purpose |
|-----------|---------|
| `frontend/src/api/` | Axios API modules (`client.ts`, `meetings.ts`, `actions.ts`, `settings.ts`, `integrations.ts`) |
| `frontend/src/pages/` | Route-level page components |
| `frontend/src/components/` | Reusable UI and feature components |
| `frontend/src/store/` | Zustand state stores |
| `frontend/src/hooks/` | Custom React hooks |
| `frontend/src/types/` | TypeScript type definitions |

### Backend

| Component | Location |
|-----------|----------|
| FastAPI app | `services/web/main.py` |
| Auth routes | `services/web/routes/auth.py` (prefix: `/api/auth`) |
| Meeting routes | `services/web/routes/meetings.py` (prefix: `/api/meetings`) |
| Action routes | `services/web/routes/actions.py` (prefix: `/api`) |
| Integration routes | `services/web/routes/integrations.py` (prefix: `/api`) |
| Device routes | `services/web/routes/device.py` (prefix: `/api/device`) |
| System routes | `services/web/routes/system.py` (prefix: `/api/system`) |

---

## Button / Interaction -> API -> Backend Map

### Login Page (`pages/Login.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Login form submit | `handleSubmit` | `POST /api/auth/login` | `routes/auth.py` | Working |

### Register Page (`pages/Register.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Register form submit | `handleSubmit` | `POST /api/auth/register` | `routes/auth.py` | Working |

### Dashboard (`pages/Dashboard.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Start Recording | `handleStartRecording` | `POST /api/meetings/start` | `routes/meetings.py` | Working |
| Stop Recording | `handleStopRecording` | `POST /api/meetings/stop` | `routes/meetings.py` | Working |
| Reset Processing | `handleResetRecording` | `POST /api/meetings/reset-recording-state` | `routes/meetings.py` | Working |
| Date filter buttons | `setFilter(f)` | None (local state, default: `'today'`) | N/A | Working |
| Search input | `setSearchQuery` | None (local filter) | N/A | Working |
| Meeting card click | navigation | None | N/A | Working |
| Delete meeting (card) | `handleDeleteMeeting` | `DELETE /api/meetings/{id}` | `routes/meetings.py` | Working |
| Polls recording status | `pollRecordingStatus` | `GET /api/meetings/recording-status` | `routes/meetings.py` | Working |
| Disk warning banner | navigates to `/system` | `GET /api/system/status` (on mount) | `routes/system.py` | Working |
| Stats: This Week | computed | None (from meetings list) | N/A | Working |
| Stats: Total Hours | computed from `duration` | None (duration now computed by backend list endpoint) | N/A | Working |
| Stats: Pending Actions | computed from `pending_actions` | None (pending_actions now returned by backend list endpoint via subquery) | N/A | Working |

### Meeting Detail (`pages/MeetingDetail.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Rename title (pencil icon) | `handleStartRename` / `handleSaveRename` | `PATCH /api/meetings/{id}` | `routes/meetings.py` | Working |
| Export PDF | `handleExport('pdf')` | `GET /api/meetings/{id}/export/pdf` | `routes/meetings.py` | Working |
| Export TXT | `handleExport('txt')` | `GET /api/meetings/{id}/export/txt` | `routes/meetings.py` | Working |
| Delete button | `setShowDeleteConfirm(true)` | None (opens modal) | N/A | Working |
| Delete confirm | `handleDeleteMeeting` | `DELETE /api/meetings/{id}` | `routes/meetings.py` | Working |
| Summarize with API | `handleSummarize` | `POST /api/meetings/{id}/summarize` (also auto-sets title from summary) | `routes/meetings.py` | Working |
| Summarize Locally | `handleSummarizeLocal` | `POST /api/meetings/{id}/summarize-local` (also auto-sets title from summary) | `routes/meetings.py` | Working |
| Tab navigation | `setActiveTab` | None (local state) | N/A | Working |
| Recording tab (audio player) | HTML5 `<audio>` | `GET /api/meetings/{id}/audio` | `routes/meetings.py` | Working |
| Action dismiss | `ActionCard.handleDismiss` | `POST /api/actions/{id}/dismiss` | `routes/actions.py` | Working |
| Action approve+execute | `ActionCard.handleExecute` | `POST /api/actions/{id}/approve` then `POST /api/actions/{id}/execute` | `routes/actions.py` | Working |

### Live Recording (`pages/LiveRecording.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Stop Recording | `handleStop` | `POST /api/meetings/stop` | `routes/meetings.py` | Working |
| WebSocket | `useWebSocket` | `ws://.../ws` | `main.py` | Working |

### Settings (`pages/Settings.tsx`)

#### General Settings (`components/settings/GeneralSettings.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Save Changes | `handleSave` | `PATCH /api/device/settings` | `routes/device.py` | Working |
| Load settings | `useEffect` | `GET /api/device/settings` | `routes/device.py` | Working |

#### Privacy Settings (`components/settings/PrivacySettings.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Auto-Record toggle | `setAutoRecord` | None (local) | N/A | Working |
| Auto-Summarize toggle | `setAutoSummarize` | None (local) | N/A | Working |
| Save Changes | `handleSave` | `PATCH /api/device/settings` (sends `auto_record`, `auto_summarize`, `auto_delete_days`, `privacy_mode`) | `routes/device.py` | Working |

#### Integrations Settings (`components/settings/IntegrationsSettings.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Connect button | `handleConnect` | `GET /api/integrations/{provider}/auth-url` then `window.location.href` redirect | `routes/integrations.py` | Working |
| OAuth callback (auto) | Google redirects to | `GET /api/integrations/{provider}/callback` (exchanges code for tokens, redirects to `/settings?integration=success`) | `routes/integrations.py` | Working |
| Disconnect button | `handleDisconnect` | `POST /api/integrations/{provider}/disconnect` | `routes/integrations.py` | Working |

### Onboarding (`pages/Onboarding.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Step 2 Continue | `handleNext` | `POST /api/auth/setup` | `routes/auth.py` | Working |
| Step 3 Continue | `handleNext` | `PATCH /api/device/settings` | `routes/device.py` | Working |
| Step 4 Continue | `handleNext` | `PATCH /api/device/settings` | `routes/device.py` | Working |
| Step 5 Connect | `handleIntegrationConnect` | `GET /api/integrations/{p}/auth-url` then redirect | `routes/integrations.py` | Working |
| Step 6 Finish | `handleNext` | `POST /api/auth/complete-onboarding` | `routes/auth.py` | Working |
| Skip | `handleSkip` | `POST /api/auth/complete-onboarding` | `routes/auth.py` | Working |

### Personal Onboarding (`pages/PersonalOnboarding.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Connect integration | `handleConnect` | `GET /api/integrations/{p}/auth-url` then redirect | `routes/integrations.py` | Working |
| Skip / Finish | `handleSkip` / `handleNext` | `POST /api/auth/complete-onboarding` | `routes/auth.py` | Working |

### System Status (`pages/SystemStatus.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Retry | inline | `GET /api/system/status` | `routes/system.py` | Working |
| Auto-refresh | `useEffect` interval | `GET /api/system/status` | `routes/system.py` | Working |
| Free up Space button | `setShowCleanup(true)` | None (opens modal) | N/A | Working |
| Delete oldest N meetings | `handleCleanup` | `POST /api/system/cleanup?count=N` | `routes/system.py` | Working |

### Navbar (`components/layout/Navbar.tsx`)

| UI Element | Handler | API Call | Backend Route | Status |
|------------|---------|----------|---------------|--------|
| Logout button | `handleLogout` | None (clears token, redirects) | N/A | Working |

---

## Frontend API Client Methods

| Module | Method | HTTP | URL | Backend Exists | Used in UI |
|--------|--------|------|-----|----------------|------------|
| `meetings.ts` | `list` | GET | `/api/meetings/` | Yes | Dashboard |
| `meetings.ts` | `get` | GET | `/api/meetings/{id}` | Yes | MeetingDetail |
| `meetings.ts` | `start` | POST | `/api/meetings/start` | Yes | Dashboard |
| `meetings.ts` | `stop` | POST | `/api/meetings/stop` | Yes | Dashboard, LiveRecording |
| `meetings.ts` | `getRecordingStatus` | GET | `/api/meetings/recording-status` | Yes | Dashboard |
| `meetings.ts` | `resetRecordingState` | POST | `/api/meetings/reset-recording-state` | Yes | Dashboard |
| `meetings.ts` | `uploadAudio` | POST | `/api/meetings/upload-audio` | Yes | Not yet |
| `meetings.ts` | `update` | PATCH | `/api/meetings/{id}` | Yes | Not yet |
| `meetings.ts` | `delete` | DELETE | `/api/meetings/{id}` | Yes | MeetingDetail, Dashboard |
| `meetings.ts` | `summarize` | POST | `/api/meetings/{id}/summarize` | Yes | MeetingDetail |
| `meetings.ts` | `summarizeLocal` | POST | `/api/meetings/{id}/summarize-local` | Yes | MeetingDetail |
| `meetings.ts` | `getAudioUrl` | GET | `/api/meetings/{id}/audio` | Yes | MeetingDetail (Recording tab) |
| `meetings.ts` | `export` | GET | `/api/meetings/{id}/export/{format}` | Yes | MeetingDetail |
| `meetings.ts` | `emailSummary` | POST | `/api/meetings/{id}/email` | Yes | Not yet |
| `meetings.ts` | `cleanupOldest` | POST | `/api/system/cleanup?count=N` | Yes | SystemStatus |
| `actions.ts` | `list` | GET | `/api/meetings/{id}/actions` | Yes | MeetingDetail |
| `actions.ts` | `approve` | POST | `/api/actions/{id}/approve` | Yes | ActionCard |
| `actions.ts` | `dismiss` | POST | `/api/actions/{id}/dismiss` | Yes | ActionCard |
| `actions.ts` | `execute` | POST | `/api/actions/{id}/execute` | Yes | ActionCard |
| `actions.ts` | `update` | PATCH | `/api/actions/{id}` | Yes | Not yet |
| `settings.ts` | `get` | GET | `/api/device/settings` | Yes | GeneralSettings, PrivacySettings |
| `settings.ts` | `update` | PATCH | `/api/device/settings` | Yes | GeneralSettings, PrivacySettings |
| `settings.ts` | `setDeviceName` | PATCH | `/api/device/settings` | Yes | Onboarding |
| `integrations.ts` | `list` | GET | `/api/integrations` | Yes | IntegrationsSettings |
| `integrations.ts` | `getAuthUrl` | GET | `/api/integrations/{p}/auth-url` | Yes | IntegrationsSettings, Onboarding |
| `integrations.ts` | `disconnect` | POST | `/api/integrations/{p}/disconnect` | Yes | IntegrationsSettings |
| `authStore.ts` | `initialize` | GET | `/api/auth/me` + `/api/auth/has-users` | Yes | App.tsx |
| `authStore.ts` | `login` | POST | `/api/auth/login` | Yes | Login |
| `authStore.ts` | `register` | POST | `/api/auth/register` | Yes | Register |
| `authStore.ts` | `completeOnboarding` | POST | `/api/auth/complete-onboarding` | Yes | Onboarding |

---

## Backend Settings Model (`services/web/routes/device.py`)

The `SettingsUpdate` Pydantic model accepts these fields:

| Field | Type | Notes |
|-------|------|-------|
| `device_name` | `Optional[str]` | |
| `timezone` | `Optional[str]` | |
| `auto_delete_days` | `Optional[str]` | `"never"` or number as string |
| `brightness` | `Optional[str]` | |
| `screen_timeout` | `Optional[str]` | |
| `privacy_mode` | `Optional[bool]` | |
| `auto_record` | `Optional[bool]` | |
| `auto_summarize` | `Optional[bool]` | |
| `action` | `Optional[str]` | `restart` / `factory_reset` |

---

## Timestamp Handling

The backend (running in a Docker container with UTC timezone) stores naive ISO timestamps without timezone info (e.g., `2026-02-26T14:20:15.123456`). The frontend must treat these as UTC by appending `Z` before parsing. Use `parseUTC()` from `utils/formatters.ts` for all backend timestamps instead of `new Date(iso)`.

## Meeting Title Auto-Generation

When a meeting is summarized (either via API or locally), the backend auto-generates a human-readable title from the summary topics or first sentence. This only replaces titles that still have the default `"Meeting {session_id}"` format. Users can also manually rename meetings via the pencil icon on the Meeting Detail page.

## Environment & Config

- Vite dev proxy: `/api` -> `http://localhost:8000`, `/ws` -> `ws://localhost:8000`
- No `.env` file in `frontend/`; uses `VITE_API_URL` env var (defaults to empty = relative URLs)
- Backend CORS: allows all origins (`*`)
- Auth: JWT Bearer token stored in `localStorage` as `auth_token`
- Axios timeout: 300s (5 min for LLM inference)

---

## WebSocket Events

Connected via `useWebSocket` hook in `LiveRecording.tsx`.

| Event Type | Source | Description |
|------------|--------|-------------|
| `audio_segment` | Redis `audio_segments` channel | Real-time transcript segment |
| `speaker_detected` | Redis `events` channel | Speaker identification |
| `recording_stopped` | Redis `events` channel | Recording finished |
