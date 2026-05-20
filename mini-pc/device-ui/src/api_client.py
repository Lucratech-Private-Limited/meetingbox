"""
Backend API Client

Handles all communication with the MeetingBox FastAPI backend.
Aligned with the actual backend routes in server/web/.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from config import (
    BACKEND_URL,
    BACKEND_WS_URL,
    API_TIMEOUT,
    WS_RECONNECT_DELAY,
    WS_MAX_RECONNECT_ATTEMPTS,
    get_device_auth_token,
    persist_device_auth_token,
    _strip_trailing_rest_api_path,
)

logger = logging.getLogger(__name__)


def _response_ok_json_api(resp: httpx.Response) -> bool:
    """True when the response looks like our JSON API, not SPA index.html (nginx mis-route)."""
    if resp.status_code != 200:
        return False
    ct = (resp.headers.get("content-type") or "").lower()
    if "text/html" in ct:
        return False
    if "json" in ct:
        return True
    sample = (resp.text or "")[:512].lstrip()
    return sample.startswith("{") or sample.startswith("[")


# Match dashboard Emails tab (`frontend/src/pages/Emails.tsx`).
_GMAIL_RECENT_DAYS = 90


def _meetings_to_calendar_days(meetings: list, start_date: str, end_date: str) -> dict:
    """
    Convert a list of local recorded meetings into the calendar/week dict format:
      {"days": {"YYYY-MM-DD": {"meetings": [...]}}}
    Used as a fallback when Google Calendar is not connected.
    """
    from datetime import date as _date
    try:
        d_start = _date.fromisoformat(start_date)
        d_end = _date.fromisoformat(end_date)
    except ValueError:
        return {"days": {}}

    days: dict = {}
    for m in meetings:
        # start_time may be in various formats
        raw_start = m.get("start_time") or m.get("started_at") or m.get("created_at") or ""
        if not raw_start:
            continue
        try:
            if raw_start.endswith("Z"):
                raw_start = raw_start[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw_start)
            day_str = dt.date().isoformat()
        except ValueError:
            continue
        try:
            meeting_date = _date.fromisoformat(day_str)
        except ValueError:
            continue
        if not (d_start <= meeting_date <= d_end):
            continue

        event = {
            "id": m.get("id", ""),
            "title": m.get("title") or "Meeting",
            "start": m.get("start_time") or raw_start,
            "end": m.get("end_time") or m.get("stop_time") or m.get("start_time") or raw_start,
            "status": m.get("status", "completed"),
            "source": "local",
        }
        days.setdefault(day_str, {"meetings": []})["meetings"].append(event)

    return {"days": days}


def _parse_sender_display(raw: str) -> str:
    """Parse 'Display Name <addr@host>' into a short display name (same idea as server emails route)."""
    m = re.match(r"^(.*?)\s*<([^>]+)>", (raw or "").strip())
    if m:
        name = m.group(1).strip().strip('"')
        addr = m.group(2).strip()
        return name or addr
    s = (raw or "").strip()
    return s or "—"


def _parse_message_date(raw_date: str) -> Optional[datetime]:
    s = (raw_date or "").strip()
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    try:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return datetime.fromisoformat(s[:10]).replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None


def _is_today_rfc(raw_date: str) -> bool:
    dt = _parse_message_date(raw_date)
    if dt is None:
        return False
    try:
        now = datetime.now(tz=timezone.utc)
        return (now - dt).days == 0
    except Exception:
        return False


def _friendly_time_rfc(raw_date: str) -> str:
    """Portable version of server emails `_friendly_time` (no platform-specific strftime)."""
    dt = _parse_message_date(raw_date)
    if dt is None:
        return (raw_date or "")[:10] if raw_date else "—"
    try:
        now = datetime.now(tz=timezone.utc)
        local_dt = dt.astimezone()
        delta_days = (now - dt).days
        if delta_days == 0:
            h24 = local_dt.hour
            h12 = h24 % 12 or 12
            ampm = "AM" if h24 < 12 else "PM"
            return f"{h12}:{local_dt.minute:02d} {ampm}"
        if delta_days < 7:
            return local_dt.strftime("%a %d")
        return local_dt.strftime("%b %d")
    except Exception:
        return raw_date[:10] if raw_date else "—"


def _map_gmail_recent_row(msg: Dict) -> Dict:
    """Shape from `GET /api/integrations/gmail/recent` `messages[]` → device EmailsScreen row."""
    raw_from = msg.get("from") or ""
    raw_date = msg.get("date") or ""
    snippet = msg.get("snippet") or ""
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "sender": _parse_sender_display(raw_from),
        "sender_email": raw_from,
        "subject": (msg.get("subject") or "(no subject)").strip() or "(no subject)",
        "preview": snippet,
        "body": snippet,
        "time": _friendly_time_rfc(raw_date),
        "date": raw_date,
        "is_today": _is_today_rfc(raw_date),
        "is_read": bool(msg.get("is_read", True)),
        "to": "",
    }


def summarize_gmail_feed_for_home(feed: Optional[Dict]) -> Dict[str, object]:
    """
    Normalize `GET /api/integrations/gmail/recent` (same as frontend `Emails.tsx`)
    for home / morning-brief widgets: unread count + one-line preview.
    """
    out: Dict[str, object] = {
        "connected": False,
        "unread_count": 0,
        "message_count": 0,
        "brief_subtitle": "Connect Gmail for updates",
        "top_raw": None,
    }
    if not isinstance(feed, dict):
        return out
    out["connected"] = bool(feed.get("connected"))
    raw_msgs = feed.get("messages")
    if not isinstance(raw_msgs, list):
        raw_msgs = []
    normalized: List[Dict] = []
    unread = 0
    for m in raw_msgs:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        normalized.append(m)
        if not m.get("is_read", True):
            unread += 1
    out["message_count"] = len(normalized)
    out["unread_count"] = unread
    err = (feed.get("error") or "").strip()
    if not out["connected"]:
        out["brief_subtitle"] = "Connect Gmail for updates"
        return out
    if not normalized:
        out["brief_subtitle"] = err or "No recent messages"
        return out
    out["top_raw"] = normalized[0]

    def _name(mi: Dict) -> str:
        return _parse_sender_display(mi.get("from") or "")

    first = _name(normalized[0])
    if len(normalized) == 1:
        subj = (normalized[0].get("subject") or normalized[0].get("snippet") or "").strip()
        out["brief_subtitle"] = f"{first}: {subj[:40]}" if subj else first
        return out
    second = _name(normalized[1])
    rest = len(normalized) - 2
    if rest > 0:
        out["brief_subtitle"] = f"{first}, {second} +{rest}"
    else:
        out["brief_subtitle"] = f"{first}, {second}"
    return out


def build_websocket_url(base_ws_url: str) -> str:
    """
    Match server/web WebSocket auth: optional MEETINGBOX_WS_REQUIRE_AUTH (access_token query)
    and/or MEETINGBOX_WS_SHARED_SECRET (token query). Harmless extras when server auth is off.
    """
    base = (base_ws_url or "").strip()
    if not base:
        return base
    parts = urlparse(base)
    q = dict(parse_qsl(parts.query, keep_blank_values=False))
    tok = (get_device_auth_token() or "").strip()
    if tok:
        q["access_token"] = tok
    secret = (
        (os.getenv("BACKEND_WS_SHARED_SECRET") or os.getenv("MEETINGBOX_WS_SHARED_SECRET") or "")
        .strip()
    )
    if secret:
        q["token"] = secret
    new_query = urlencode(q) if q else ""
    return urlunparse(
        (parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment)
    )


def invoke_realtime_tool_sync(
    base_url: str,
    bearer_token: str,
    *,
    call_id: str,
    name: str,
    arguments: str = "{}",
    timeout: float = 90.0,
) -> str:
    """
    POST /api/voice/realtime/tools/invoke (blocking).
    Used by the Realtime WebSocket worker thread; mirrors BackendClient.invoke_realtime_tool.
    """
    root = _strip_trailing_rest_api_path((base_url or "").strip().rstrip("/"))
    tok = (bearer_token or "").strip()
    if not root or not tok:
        return json.dumps({"error": "missing_backend_or_token"})
    url = f"{root}/api/voice/realtime/tools/invoke"
    headers = {"Authorization": f"Bearer {tok}"}
    payload = {
        "call_id": (call_id or "").strip(),
        "name": (name or "").strip(),
        "arguments": arguments if arguments is not None else "{}",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
        if isinstance(body, dict) and "output" in body:
            return str(body["output"])
        return ""
    except Exception as e:
        logger.warning("invoke_realtime_tool_sync failed: %s", e)
        return json.dumps({"error": "invoke_failed", "detail": str(e)})


class BackendClient:
    """
    Client for MeetingBox backend API.

    Route mapping (actual backend):
      Health:          GET  /health
      Start recording: POST /api/meetings/start
      Stop recording:  POST /api/meetings/stop
      Recording state: GET  /api/meetings/recording-status
      Pause:           POST /api/meetings/pause           (device route)
      Resume:          POST /api/meetings/resume           (device route)
      List meetings:   GET  /api/meetings/
      Meeting detail:  GET  /api/meetings/{id}
      Delete meeting:  DELETE /api/meetings/{id}           (device route)
      System status:   GET  /api/system/status
      Device info:     GET  /api/system/device-info        (device route)
      Settings:        GET  /api/device/settings           (device route)
      Settings update: PATCH /api/device/settings          (device route)
      WiFi scan:       GET  /api/device/wifi/scan          (device route)
      WiFi connect:    POST /api/device/wifi/connect       (device route)
      Check updates:   GET  /api/device/check-updates      (device route)
      Install update:  POST /api/device/install-update     (device route)
      WebSocket:       ws://host:port/ws (?access_token= / ?token= when server requires it)
      Claim pairing:   POST /api/devices/claim              (no auth)
    """

    def __init__(self, base_url: str = BACKEND_URL):
        self.base_url = _strip_trailing_rest_api_path(base_url.rstrip("/"))
        self.ws_url = BACKEND_WS_URL
        # Short connect timeout so unreachable hosts fail fast; read/write use API_TIMEOUT.
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                float(API_TIMEOUT),
                connect=min(10.0, float(API_TIMEOUT)),
            ),
        )
        self._refresh_auth_header()
        self.ws_connection = None
        self._ws_reconnect_attempts = 0

    def _refresh_auth_header(self) -> None:
        """Re-read the device auth token and update the httpx client header.

        Called at init and after claim_device so the token is always current
        without requiring a full client restart.
        """
        token = (get_device_auth_token() or "").strip()
        if token:
            self.client.headers["Authorization"] = f"Bearer {token}"
        else:
            self.client.headers.pop("Authorization", None)

    async def close(self):
        await self.client.aclose()
        if self.ws_connection:
            await self.ws_connection.close()

    def set_device_auth_header(self, token: Optional[str]) -> None:
        t = (token or "").strip()
        if t:
            self.client.headers["Authorization"] = f"Bearer {t}"
        else:
            self.client.headers.pop("Authorization", None)

    async def claim_device(
        self,
        code: str,
        device_name: Optional[str] = None,
        serial_number: Optional[str] = None,
    ) -> Dict:
        """
        POST /api/devices/claim (no auth). Returns device + access_token; persists token.
        """
        payload: Dict[str, str] = {"code": (code or "").strip()}
        if device_name is not None:
            dn = (device_name or "").strip()
            if dn:
                payload["device_name"] = dn
        if serial_number:
            sn = serial_number.strip()
            if sn:
                payload["serial_number"] = sn
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as raw:
            resp = await raw.post(
                f"{self.base_url}/api/devices/claim",
                json=payload,
            )
        resp.raise_for_status()
        data = resp.json()
        access = (data.get("access_token") or "").strip()
        if not access:
            raise ValueError("Claim response missing access_token")
        persist_device_auth_token(access)
        self._refresh_auth_header()
        return data

    # ==================================================================
    # MEETINGS API
    # ==================================================================

    async def start_recording(self) -> Dict:
        """
        POST /api/meetings/start
        Returns: { session_id, status }
        """
        try:
            resp = await self.client.post(f"{self.base_url}/api/meetings/start")
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Started recording: {data.get('session_id')}")
            return data
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            raise

    async def stop_recording(self, session_id: str = None) -> Dict:
        """
        POST /api/meetings/stop
        Backend reads current session from Redis, session_id param unused.
        Returns: { session_id, status }
        """
        try:
            resp = await self.client.post(f"{self.base_url}/api/meetings/stop")
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Stopped recording: {data.get('session_id')}")
            return data
        except Exception as e:
            logger.error(f"Failed to stop recording: {e}")
            raise

    async def pause_recording(self, session_id: str) -> Dict:
        """
        POST /api/meetings/pause
        Sends pause command to audio service via Redis.
        """
        try:
            resp = await self.client.post(f"{self.base_url}/api/meetings/pause")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to pause recording: {e}")
            raise

    async def resume_recording(self, session_id: str) -> Dict:
        """
        POST /api/meetings/resume
        Sends resume command to audio service via Redis.
        """
        try:
            resp = await self.client.post(f"{self.base_url}/api/meetings/resume")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to resume recording: {e}")
            raise

    async def get_recording_status(self) -> Dict:
        """
        GET /api/meetings/recording-status
        Returns: { state, session_id }
        """
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/meetings/recording-status")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to get recording status: {e}")
            raise

    async def get_meetings(self, limit: int = 20, offset: int = 0) -> List[Dict]:
        """
        GET /api/meetings/?limit=&offset=
        Returns list of meeting dicts. Returns [] on any error so callers never crash.
        """
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/meetings/",
                params={"limit": limit, "offset": offset},
            )
            resp.raise_for_status()
            meetings = resp.json()
            if not isinstance(meetings, list):
                return []
            for m in meetings:
                m.setdefault('pending_actions', 0)
            return meetings
        except httpx.HTTPStatusError as e:
            logger.warning("get_meetings HTTP %s: %s", e.response.status_code, e.response.text[:200])
            return []
        except Exception as e:
            logger.warning("get_meetings failed: %s", e)
            return []

    async def get_meeting_detail(self, meeting_id: str) -> Dict:
        """
        GET /api/meetings/{meeting_id}
        Backend returns: { meeting: {...}, segments: [...], summary: {...}|null,
                           local_summary: {...}|null }
        We flatten to the shape the UI expects.
        """
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/meetings/{meeting_id}")
            resp.raise_for_status()
            data = resp.json()

            # Flatten: merge meeting fields + summary into a single dict
            meeting = data.get('meeting', {})
            segments = data.get('segments', [])
            summary_raw = data.get('summary')
            local_raw = data.get('local_summary')
            if summary_raw and isinstance(summary_raw, dict):
                summary = dict(summary_raw)
                if (
                    not (summary.get('action_items') or [])
                    and local_raw
                    and isinstance(local_raw, dict)
                ):
                    la = local_raw.get('action_items') or []
                    if la:
                        summary['action_items'] = la
            elif local_raw and isinstance(local_raw, dict):
                summary = local_raw
            else:
                summary = {}

            result = {
                **meeting,
                'segments': segments,
                'summary': summary,
            }
            return result
        except Exception as e:
            logger.error(f"Failed to fetch meeting {meeting_id}: {e}")
            raise

    async def delete_meeting(self, meeting_id: str) -> None:
        """DELETE /api/meetings/{meeting_id}"""
        try:
            resp = await self.client.delete(
                f"{self.base_url}/api/meetings/{meeting_id}")
            resp.raise_for_status()
            logger.info(f"Deleted meeting: {meeting_id}")
        except Exception as e:
            logger.error(f"Failed to delete meeting {meeting_id}: {e}")
            raise

    # ==================================================================
    # SUMMARIZE API
    # ==================================================================

    async def summarize_meeting(self, meeting_id: str) -> Dict:
        """POST /api/meetings/{meeting_id}/summarize (Claude API)"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/meetings/{meeting_id}/summarize",
                timeout=300.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to summarize meeting {meeting_id}: {e}")
            raise

    # ==================================================================
    # ACTIONS API
    # ==================================================================

    async def get_actions(self, meeting_id: str) -> List[Dict]:
        """GET /api/meetings/{meeting_id}/actions"""
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/meetings/{meeting_id}/actions")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to get actions for meeting {meeting_id}: {e}")
            raise

    async def generate_actions(self, meeting_id: str) -> List[Dict]:
        """POST /api/meetings/{meeting_id}/actions/generate"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/meetings/{meeting_id}/actions/generate",
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to generate actions for meeting {meeting_id}: {e}")
            raise

    async def execute_action(
            self,
            action_id: str,
            *,
            create_draft: bool = False,
            repeat_execution: bool = False,
    ) -> Dict:
        """POST /api/actions/{action_id}/execute — Gmail: create_draft saves to Gmail drafts instead of sending."""
        try:
            body: Dict = {}
            if create_draft:
                body["create_draft"] = True
            if repeat_execution:
                body["repeat_execution"] = True
            resp = await self.client.post(
                f"{self.base_url}/api/actions/{action_id}/execute",
                json=body,
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                data = e.response.json()
                if isinstance(data, dict) and data.get("detail") is not None:
                    d = data["detail"]
                    detail = d if isinstance(d, str) else json.dumps(d)
            except Exception:
                detail = (e.response.text or "")[:500]
            msg = (detail or e.response.reason_phrase or str(e)).strip()
            logger.error("Failed to execute action %s: HTTP %s %s", action_id, e.response.status_code, msg)
            raise RuntimeError(msg) from e
        except Exception as e:
            logger.error(f"Failed to execute action {action_id}: {e}")
            raise

    async def dismiss_action(self, action_id: str) -> Dict:
        """POST /api/actions/{action_id}/dismiss"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/actions/{action_id}/dismiss")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to dismiss action {action_id}: {e}")
            raise

    # ==================================================================
    # ASSISTANT API
    # ==================================================================

    async def post_assistant_intent(
            self,
            message: str,
            meeting_id: Optional[str] = None,
    ) -> Dict:
        """POST /api/assistant/intent — route a natural-language request.
        Raises on failure so callers (voice assistant) can distinguish network
        errors from empty responses.
        """
        payload: Dict = {"message": message}
        if meeting_id:
            payload["meeting_id"] = meeting_id
        resp = await self.client.post(
            f"{self.base_url}/api/assistant/intent",
            json=payload,
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def approve_assistant_pending(self, pending_id: str) -> Dict:
        """POST /api/assistant/pending-actions/{id}/approve"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/assistant/pending-actions/{pending_id}/approve",
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("approve_assistant_pending failed: %s", e)
            raise

    async def reject_assistant_pending(self, pending_id: str) -> Dict:
        """POST /api/assistant/pending-actions/{id}/reject"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/assistant/pending-actions/{pending_id}/reject",
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("reject_assistant_pending failed: %s", e)
            raise

    async def patch_assistant_pending_payload(
            self, pending_id: str, payload: Dict) -> Dict:
        """PATCH /api/assistant/pending-actions/{id} (email or calendar draft)."""
        try:
            resp = await self.client.patch(
                f"{self.base_url}/api/assistant/pending-actions/{pending_id}",
                json={"payload": payload},
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("patch_assistant_pending_payload failed: %s", e)
            raise

    # ==================================================================
    # SETTINGS API (device route)
    # ==================================================================

    async def get_pairing_status(self) -> Dict:
        """GET /api/device/pairing-status — 401 if unpaired from dashboard."""
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/device/pairing-status")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("Pairing status failed: %s", e)
            raise

    async def unpair_self(self) -> Dict:
        """POST /api/device/unpair-self — unlink this device from owner account."""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/device/unpair-self")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("Unpair self failed: %s", e)
            raise

    async def get_settings(self) -> Dict:
        """GET /api/device/settings"""
        try:
            resp = await self.client.get(f"{self.base_url}/api/device/settings")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch settings: {e}")
            raise

    async def update_settings(self, settings: Dict) -> Dict:
        """PATCH /api/device/settings"""
        try:
            resp = await self.client.patch(
                f"{self.base_url}/api/device/settings", json=settings)
            resp.raise_for_status()
            logger.info(f"Updated settings: {settings}")
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to update settings: {e}")
            raise

    async def post_setup_complete(
            self,
            wifi_ssid: str = "",
            onboarding_flow: str = "wifi_on_device_v1") -> Dict:
        """POST /api/device/setup-complete — writes .setup_complete JSON on server."""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/device/setup-complete",
                json={
                    "wifi_ssid": wifi_ssid or "",
                    "onboarding_flow": onboarding_flow,
                },
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to mark setup complete: {e}")
            raise

    # ==================================================================
    # INTEGRATIONS API
    # ==================================================================

    async def get_integrations(self) -> List[Dict]:
        """GET /api/device/integrations"""
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/device/integrations")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch integrations: {e}")
            raise

    async def get_integration_auth_url(self, integration_id: str) -> str:
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/device/integrations/{integration_id}/auth-url")
            resp.raise_for_status()
            data = resp.json()
            return data.get('auth_url') or data.get('url') or ''
        except Exception as e:
            logger.error(f"Failed to get auth URL for {integration_id}: {e}")
            raise

    async def disconnect_integration(self, integration_id: str) -> None:
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/device/integrations/{integration_id}/disconnect")
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to disconnect {integration_id}: {e}")
            raise

    # ==================================================================
    # CALENDAR API
    # ==================================================================

    async def get_calendar_week(self, start_date: str, end_date: str) -> Dict:
        """GET /api/calendar/week?start=YYYY-MM-DD&end=YYYY-MM-DD

        Returns meetings grouped by date:
          {"days": {"2026-05-04": {"meetings": [{id, title, start, end, ...}]}}}

        Falls back to local recorded meetings when Google Calendar is not connected.
        """
        # --- Primary: Google Calendar via server ---
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/calendar/week",
                params={"start": start_date, "end": end_date},
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "days" in data:
                return data
        except Exception as e:
            logger.debug("get_calendar_week google failed: %s — using meetings fallback", e)

        # --- Fallback: build calendar view from local recorded meetings ---
        try:
            meetings = await self.get_meetings(limit=100)
            return _meetings_to_calendar_days(meetings, start_date, end_date)
        except Exception as e:
            logger.debug("get_calendar_week fallback failed: %s", e)

        return {"days": {}}

    async def get_briefing_context(self, days_ahead: int = 1) -> Dict:
        """GET /api/briefing/context — calendar slice, tasks, mem0, Gmail preview."""
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/briefing/context",
                params={"days_ahead": int(days_ahead)},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug("get_briefing_context failed: %s", e)
            return {}

    async def get_commitments(self, status: str = "", limit: int = 40) -> Dict:
        """GET /api/commitments"""
        try:
            params: Dict = {"limit": int(limit)}
            if (status or "").strip():
                params["status"] = status.strip()
            resp = await self.client.get(
                f"{self.base_url}/api/commitments",
                params=params,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug("get_commitments failed: %s", e)
            return {"commitments": [], "count": 0}

    async def create_realtime_voice_session(self) -> Dict:
        """POST /api/voice/realtime/session — OpenAI Realtime client secret (Bearer token)."""
        resp = await self.client.post(f"{self.base_url}/api/voice/realtime/session")
        resp.raise_for_status()
        return resp.json()

    async def invoke_realtime_tool(
        self,
        *,
        call_id: str,
        name: str,
        arguments: str = "{}",
    ) -> str:
        """POST /api/voice/realtime/tools/invoke — Mem0 / briefing tools for Realtime voice."""
        resp = await self.client.post(
            f"{self.base_url}/api/voice/realtime/tools/invoke",
            json={
                "call_id": (call_id or "").strip(),
                "name": (name or "").strip(),
                "arguments": arguments if (arguments is not None) else "{}",
            },
        )
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and "output" in body:
            return str(body["output"])
        return ""

    # ==================================================================
    # GMAIL (same feed as dashboard: GET /api/integrations/gmail/recent)
    # ==================================================================

    async def fetch_gmail_recent(
            self,
            *,
            max_results: int = 40,
            days: int = _GMAIL_RECENT_DAYS,
            q: str = "",
            folder: str = "all",
    ) -> Dict:
        """
        Fetch Gmail messages. folder= "all"|"today"|"unread"|"sent"|"drafts".
        Tries the dedicated /api/emails endpoint first (purpose-built for device
        + user Bearer), then falls back to /api/integrations/gmail/recent.
        """
        # --- Primary: /api/emails (returns list directly) ---
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/emails",
                params={"filter": folder, "limit": int(max_results)},
            )
            resp.raise_for_status()
            rows = resp.json()
            if isinstance(rows, list):
                return {"connected": True, "messages": rows, "count": len(rows)}
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in (401, 403):
                logger.debug("fetch_gmail_recent /api/emails HTTP %s", e.response.status_code)
            # Fall through to legacy endpoint on auth errors too (device may not be paired yet)
        except Exception as e:
            logger.debug("fetch_gmail_recent /api/emails failed: %s", e)

        # --- Fallback: /api/integrations/gmail/recent ---
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/integrations/gmail/recent",
                params={
                    "max_results": int(max_results),
                    "days": int(days),
                    "q": (q or "").strip(),
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                return {"connected": False, "messages": [], "error": "Invalid response"}
            # This endpoint returns raw Gmail API format (from/date/snippet keys).
            # Pre-map to device format here so callers always receive shaped data.
            raw_msgs = data.get("messages") or []
            mapped = [
                _map_gmail_recent_row(m)
                for m in raw_msgs
                if isinstance(m, dict) and m.get("id")
            ]
            return {
                "connected": bool(data.get("connected")),
                "messages": mapped,
                "count": len(mapped),
                "error": data.get("error"),
            }
        except httpx.HTTPStatusError as e:
            sc = e.response.status_code
            logger.warning("fetch_gmail_recent HTTP %s: %s", sc, (e.response.text or "")[:300])
            if sc == 401:
                error_msg = "HTTP 401 — not authenticated"
            elif sc == 403:
                error_msg = "HTTP 403 — Gmail not connected"
            else:
                error_msg = f"HTTP {sc}"
            return {"connected": False, "messages": [], "error": error_msg}
        except httpx.TimeoutException as e:
            logger.warning("fetch_gmail_recent timeout: %s", e)
            return {"connected": False, "messages": [], "error": "timeout — network or server issue"}
        except Exception as e:
            logger.warning("fetch_gmail_recent failed: %s", e)
            return {"connected": False, "messages": [], "error": str(e)}

    async def get_emails(self, filter: str = "all", limit: int = 50) -> List[Dict]:
        """Load Gmail rows using the same API as the web dashboard (filter applied locally by EmailsScreen)."""
        data = await self.fetch_gmail_recent(max_results=limit, days=_GMAIL_RECENT_DAYS, q="")
        if not data.get("connected"):
            err = (data.get("error") or "").strip()
            if err:
                logger.warning("get_emails: Gmail not connected: %s", err[:300])
            else:
                logger.warning("get_emails: connected=false (connect Gmail from dashboard Settings)")
            return []
        rows = data.get("messages") or []
        if not isinstance(rows, list):
            return []
        return [_map_gmail_recent_row(m) for m in rows if isinstance(m, dict) and m.get("id")]

    async def get_email_detail(self, email_id: str) -> Dict:
        """GET /api/emails/{id} — full body (falls back to integrations route)."""
        for url in (
            f"{self.base_url}/api/emails/{email_id}",
            f"{self.base_url}/api/integrations/gmail/messages/{email_id}",
        ):
            try:
                resp = await self.client.get(url)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.debug("get_email_detail %s failed: %s", url, e)
        return {}

    async def mark_email_unread(self, email_id: str) -> Dict:
        """POST /api/emails/{id}/mark-unread (falls back to integrations route)."""
        for url in (
            f"{self.base_url}/api/emails/{email_id}/mark-unread",
            f"{self.base_url}/api/integrations/gmail/messages/{email_id}/mark-unread",
        ):
            try:
                resp = await self.client.post(url)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.debug("mark_email_unread %s failed: %s", url, e)
        return {}

    async def mark_email_read(self, email_id: str) -> Dict:
        """POST /api/emails/{id}/mark-read (falls back to integrations route)."""
        for url in (
            f"{self.base_url}/api/emails/{email_id}/mark-read",
            f"{self.base_url}/api/integrations/gmail/messages/{email_id}/mark-read",
        ):
            try:
                resp = await self.client.post(url)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.debug("mark_email_read %s failed: %s", url, e)
        return {}

    async def archive_email(self, email_id: str) -> Dict:
        """POST /api/emails/{id}/archive (falls back to integrations route)."""
        for url in (
            f"{self.base_url}/api/emails/{email_id}/archive",
            f"{self.base_url}/api/integrations/gmail/messages/{email_id}/archive",
        ):
            try:
                resp = await self.client.post(url)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.debug("archive_email %s failed: %s", url, e)
        return {}

    # ==================================================================
    # SYSTEM API
    # ==================================================================

    async def get_home_summary(self) -> Dict:
        """GET /api/device/home-summary — calendar + pending action counts (needs device/user auth)."""
        try:
            resp = await self.client.get(f"{self.base_url}/api/device/home-summary")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug("home-summary unavailable: %s", e)
            return {
                "next_meeting": None,
                "pending_actions_today": 0,
                "pending_actions_total": 0,
            }

    async def get_system_info(self) -> Dict:
        """
        GET /api/system/device-info
        Returns device-level info (name, firmware, WiFi, storage, uptime).
        Falls back to /api/system/status if device-info not available.
        """
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/system/device-info")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError:
            # Fallback: use /api/system/status and normalise
            try:
                resp2 = await self.client.get(
                    f"{self.base_url}/api/system/status")
                resp2.raise_for_status()
                raw = resp2.json().get('system', {})
                return {
                    'device_name': 'MeetingBox',
                    'firmware_version': '1.0.0',
                    'ip_address': '',
                    'wifi_ssid': '',
                    'wifi_signal': 0,
                    'storage_used': int(raw.get('disk_used_gb', 0) * (1024**3)),
                    'storage_total': int(raw.get('disk_total_gb', 1) * (1024**3)),
                    'uptime': 0,
                    'meetings_count': 0,
                }
            except Exception:
                raise
        except Exception as e:
            logger.error(f"Failed to fetch system info: {e}")
            raise

    async def post_appliance_system_metrics(self, metrics: Dict) -> None:
        """POST /api/device/system-metrics — appliance CPU/RAM/disk for web dashboard."""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/device/system-metrics",
                json=metrics,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.debug("post appliance system-metrics: %s", e)
            raise

    async def check_for_updates(self) -> Dict:
        """GET /api/device/check-updates"""
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/device/check-updates")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to check for updates: {e}")
            raise

    async def install_update(self) -> Dict:
        """POST /api/device/install-update"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/device/install-update")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to install update: {e}")
            raise

    # ==================================================================
    # WIFI API (device route)
    # ==================================================================

    async def get_wifi_networks(self) -> List[Dict]:
        """GET /api/device/wifi/scan"""
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/device/wifi/scan")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to scan WiFi: {e}")
            raise

    async def connect_wifi(self, ssid: str, password: str = None) -> Dict:
        """POST /api/device/wifi/connect"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/device/wifi/connect",
                json={"ssid": ssid, "password": password},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to connect to WiFi {ssid}: {e}")
            raise

    async def disconnect_wifi(self) -> None:
        """POST /api/device/wifi/disconnect"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/device/wifi/disconnect")
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to disconnect WiFi: {e}")
            raise

    async def start_mic_test(self) -> Dict:
        """POST /api/device/mic-test/start"""
        try:
            resp = await self.client.post(f"{self.base_url}/api/device/mic-test/start")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to start mic test: {e}")
            raise

    async def stop_mic_test(self) -> Dict:
        """POST /api/device/mic-test/stop"""
        try:
            resp = await self.client.post(f"{self.base_url}/api/device/mic-test/stop")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to stop mic test: {e}")
            raise

    # ==================================================================
    # WEBSOCKET (Real-time events)
    # ==================================================================

    async def subscribe_events(self) -> AsyncIterator[Dict]:
        """
        Subscribe to real-time events from backend via WebSocket at /ws.
        Auto-reconnects on disconnect.
        """
        while True:
            try:
                ws_connect_url = build_websocket_url(self.ws_url)
                async with websockets.connect(
                    ws_connect_url,
                    open_timeout=12,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=None,
                ) as ws:
                    logger.info("WebSocket connected")
                    self._ws_reconnect_attempts = 0
                    self.ws_connection = ws

                    async for message in ws:
                        try:
                            event = json.loads(message)
                            if 'type' in event:
                                yield event
                            elif 'segment_num' in event:
                                yield {'type': 'audio_segment', 'data': event}
                            else:
                                yield {'type': 'unknown', 'data': event}
                        except json.JSONDecodeError:
                            continue

            except ConnectionClosed:
                logger.warning("WebSocket closed, reconnecting…")
                self.ws_connection = None
                await self._handle_reconnect()
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self.ws_connection = None
                await self._handle_reconnect()

    async def _handle_reconnect(self):
        self._ws_reconnect_attempts += 1
        if self._ws_reconnect_attempts > WS_MAX_RECONNECT_ATTEMPTS:
            logger.error("Max WS reconnect attempts reached")
            raise ConnectionError("Failed to reconnect to backend WebSocket")
        exp = max(0, self._ws_reconnect_attempts - 1)
        delay = min(WS_RECONNECT_DELAY * (2**exp), 30)
        logger.info(f"Reconnecting WS in {delay}s (attempt {self._ws_reconnect_attempts})")
        await asyncio.sleep(delay)

    # ==================================================================
    # HEALTH CHECK
    # ==================================================================

    async def health_check(self) -> bool:
        """
        Reachability probe: many deployments only reverse-proxy ``/api/*`` to FastAPI, so
        ``GET {base}/health`` never hits the API (404) or returns SPA HTML (200 + wrong body).
        Try ``/health`` first, then ``/api/system/status`` (usually proxied; optional auth off by default).

        Uses a dedicated one-shot client so the probe is never blocked waiting for a connection
        from the shared pool (which may be busy with concurrent startup requests or long-polls).
        """
        probes = (
            f"{self.base_url}/health",
            f"{self.base_url}/api/system/status",
        )
        last_err: Exception | None = None
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=12.0, read=15.0, write=8.0, pool=8.0),
            follow_redirects=True,
        ) as probe_client:
            for url in probes:
                for attempt in range(2):
                    try:
                        resp = await probe_client.get(url)
                        if _response_ok_json_api(resp):
                            return True
                        logger.warning(
                            "Health probe not OK: %s → HTTP %s (not JSON API)",
                            url,
                            resp.status_code,
                        )
                    except Exception as e:
                        last_err = e
                        logger.warning(
                            "Health probe failed: %s (attempt %d/2) — %s",
                            url,
                            attempt + 1,
                            e,
                        )
                        if attempt == 0:
                            await asyncio.sleep(0.5)
        if last_err is not None:
            logger.error("All health probes failed (last error: %s)", last_err)
        return False

