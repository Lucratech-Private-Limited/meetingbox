"""
Google Integrations Routes -- Standard OAuth 2.0 Authorization Code flow
for Gmail and Google Calendar.

Uses the standard redirect-based OAuth flow: user clicks Connect, is redirected
to Google's consent screen, authorizes, and is redirected back to the app with
an authorization code that is exchanged for tokens.

Env vars required:
  GOOGLE_CLIENT_ID      -- from Google Cloud Console (Web application type)
  GOOGLE_CLIENT_SECRET  -- from Google Cloud Console
  APP_BASE_URL          -- fallback base URL if Host cannot be determined (e.g. http://localhost:8000)
  OAUTH_PUBLIC_BASE_URL -- optional; same as auth routes — forces redirect_uri / post-OAuth origin
                           (e.g. http://meetingbox.example.com:8000). Must match Google Console exactly.
  FRONTEND_BASE_URL     -- optional; if set, browser is sent here after OAuth (e.g. .../settings)

OAuth redirect_uri uses OAUTH_PUBLIC_BASE_URL when set (aligned with /api/auth/google/callback).
Otherwise it is derived from the incoming request (X-Forwarded-Proto + Host), then APP_BASE_URL.
"""

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, quote_plus

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from auth import get_current_actor, get_current_user, SECRET_KEY
from database import get_connection

logger = logging.getLogger(__name__)

router = APIRouter()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
OAUTH_PUBLIC_BASE_URL = os.getenv("OAUTH_PUBLIC_BASE_URL", "").strip().rstrip("/")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "").strip().rstrip("/")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

SCOPES_BY_PROVIDER = {
    "gmail": " ".join([
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
    ]),
    "calendar": " ".join([
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
    ]),
}

INTEGRATION_CAPABILITIES = {
    "gmail": {
        "connector_target": "gmail",
        "action_kinds": ["followup_email"],
        "execution_modes": ["message_send"],
        "description": "Send stakeholder recap and follow-up emails.",
    },
    "calendar": {
        "connector_target": "calendar",
        "action_kinds": ["schedule_followup"],
        "execution_modes": ["event_create"],
        "description": "Create follow-up meetings and focus blocks.",
    },
}


def _check_google_configured():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars.",
        )


def _get_redirect_uri(provider: str) -> str:
    base = OAUTH_PUBLIC_BASE_URL or APP_BASE_URL
    return f"{base}/api/integrations/{provider}/callback"


def infer_public_base_url(request: Request) -> str:
    """
    Public origin (scheme + host [+ port]) for OAuth redirect_uri and return redirects.
    Uses OAUTH_PUBLIC_BASE_URL when set; else proxy Host headers; else APP_BASE_URL.
    """
    if OAUTH_PUBLIC_BASE_URL:
        return OAUTH_PUBLIC_BASE_URL

    raw_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").strip()
    if raw_host:
        host = raw_host.split(",")[0].strip()
    else:
        host = ""

    if host:
        proto_raw = (request.headers.get("x-forwarded-proto") or "").strip().lower()
        proto = proto_raw.split(",")[0].strip() if proto_raw else ""
        if proto not in ("http", "https"):
            proto = request.url.scheme or "http"
        hostname_only = host.split(":")[0].lower()
        if hostname_only in ("web", "meetingbox-web"):
            logger.warning(
                "OAuth infer_public_base_url: internal Host %r — using APP_BASE_URL", host
            )
            return APP_BASE_URL.rstrip("/")
        return f"{proto}://{host}".rstrip("/")

    # No Host (e.g. odd clients): fall back
    return APP_BASE_URL.rstrip("/")


def _redirect_uri_for_request(request: Request, provider: str) -> str:
    return f"{infer_public_base_url(request)}/api/integrations/{provider}/callback"


def _post_oauth_browser_base(request: Request) -> str:
    """Where to send the browser after OAuth (SPA /settings). Prefer FRONTEND_BASE_URL if set."""
    if FRONTEND_BASE_URL:
        return FRONTEND_BASE_URL
    return infer_public_base_url(request)


def _create_state_token(user_id: str, provider: str) -> str:
    """Create a signed JWT containing the user_id and provider for CSRF protection."""
    from jose import jwt as jose_jwt
    secret = SECRET_KEY
    return jose_jwt.encode(
        {"sub": user_id, "provider": provider, "nonce": uuid.uuid4().hex},
        secret,
        algorithm="HS256",
    )


def _verify_state_token(state: str) -> dict:
    """Verify and decode the state JWT. Returns {"sub": user_id, "provider": ...}."""
    from jose import jwt as jose_jwt, JWTError
    secret = SECRET_KEY
    try:
        return jose_jwt.decode(state, secret, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")


def _get_integration(user_id: str, provider: str) -> dict | None:
    conn = get_connection()
    conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM integrations WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        )
        return cur.fetchone()
    finally:
        conn.close()


def get_connected_providers(user_id: str) -> list[str]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT provider FROM integrations WHERE user_id = ?", (user_id,))
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def get_action_capabilities(user_id: str | None) -> list[dict]:
    capabilities = [
        {
            "connector_target": "internal",
            "action_kinds": ["cost_analysis", "decision_brief", "risk_register", "task_digest"],
            "execution_modes": ["artifact_create"],
            "description": "Create and save internal MeetingBox artifacts.",
        }
    ]
    if not user_id:
        return capabilities

    for provider in get_connected_providers(user_id):
        meta = INTEGRATION_CAPABILITIES.get(provider)
        if meta:
            capabilities.append(meta)
    return capabilities


def _build_credentials(integration: dict):
    """Build a google.oauth2.credentials.Credentials object from stored tokens."""
    from google.oauth2.credentials import Credentials

    expiry = None
    if integration.get("token_expiry"):
        try:
            expiry = datetime.fromisoformat(integration["token_expiry"])
        except (ValueError, TypeError):
            pass

    return Credentials(
        token=integration["access_token"],
        refresh_token=integration["refresh_token"],
        token_uri=TOKEN_URL,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        expiry=expiry,
        scopes=integration.get("scopes", "").split(" ") if integration.get("scopes") else [],
    )


def get_credentials_for_provider(user_id: str, provider: str):
    """Public helper used by the actions system. Returns refreshed Credentials or None."""
    integration = _get_integration(user_id, provider)
    if not integration:
        return None

    creds = _build_credentials(integration)

    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request as GoogleRequest
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                creds.refresh(GoogleRequest())
                conn = get_connection()
                try:
                    conn.execute(
                        "UPDATE integrations SET access_token = ?, token_expiry = ? WHERE id = ?",
                        (creds.token, creds.expiry.isoformat() if creds.expiry else None, integration["id"]),
                    )
                    conn.commit()
                finally:
                    conn.close()
                break
            except Exception as e:
                last_err = e
                logger.warning(
                    "Token refresh attempt %d failed for %s/%s: %s",
                    attempt + 1,
                    user_id,
                    provider,
                    e,
                )
                if attempt == 0:
                    time.sleep(0.5)
        else:
            logger.error("Token refresh failed for %s/%s: %s", user_id, provider, last_err)
            return None

    return creds


def _save_tokens(user_id: str, provider: str, token_data: dict, scopes: str):
    """Persist OAuth tokens and fetch the Google user email."""
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in")

    expiry = None
    if expires_in:
        from datetime import timedelta
        expiry = (datetime.utcnow() + timedelta(seconds=int(expires_in))).isoformat()

    email = ""
    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=TOKEN_URL,
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
        )
        from googleapiclient.discovery import build
        service = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        user_info = service.userinfo().get().execute()
        email = user_info.get("email", "")
    except Exception as e:
        logger.warning("Could not fetch Google user email: %s", e)

    integration_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM integrations WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        )
        conn.execute(
            """INSERT INTO integrations
               (id, user_id, provider, scopes, access_token, refresh_token, token_expiry, email, connected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (integration_id, user_id, provider, scopes, access_token, refresh_token, expiry, email, now),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Connected %s for user %s (email: %s)", provider, user_id, email)
    return {"email": email}


# ======================================================================
# LIST INTEGRATIONS
# ======================================================================

@router.get("/integrations")
async def list_integrations(current_user: dict = Depends(get_current_user)):
    """Return connection status for all supported integrations."""
    user_id = current_user["id"]
    results = []

    for provider_id, meta in [
        ("gmail", {"name": "Gmail", "icon": "mail", "description": "Send AI-drafted emails from meeting action items"}),
        ("calendar", {"name": "Google Calendar", "icon": "calendar", "description": "Create calendar events from meeting action items"}),
    ]:
        integration = _get_integration(user_id, provider_id)
        last_sync_val = integration.get("connected_at") if integration else None
        results.append({
            "id": provider_id,
            "name": meta["name"],
            "icon": meta["icon"],
            "description": meta["description"],
            "connected": integration is not None,
            "email": integration["email"] if integration else None,
            "last_sync": last_sync_val,
        })

    return results


# ======================================================================
# READ-ONLY CALENDAR / GMAIL FEEDS FOR DASHBOARD (must be declared before `/{provider}/auth-url`)
# ======================================================================

def _calendar_feed_error_payload(http_status: int, message: str) -> dict:
    """Structured JSON when Calendar API fails after OAuth row exists."""
    if http_status in (401,):
        return {
            "connected": False,
            "events": [],
            "count": 0,
            "error": message or "Reconnect Google Calendar in Settings.",
            "google_status": http_status,
        }
    if http_status == 429:
        return {
            "connected": True,
            "events": [],
            "count": 0,
            "error": "Google Calendar rate limited. Try again shortly.",
            "google_status": http_status,
        }
    return {
        "connected": True,
        "events": [],
        "count": 0,
        "error": message or "Could not load Google Calendar.",
        "google_status": http_status,
    }


def _gmail_feed_error_payload(http_status: int, message: str) -> dict:
    if http_status in (401,):
        return {
            "connected": False,
            "messages": [],
            "count": 0,
            "error": message or "Reconnect Gmail in Settings.",
            "google_status": http_status,
        }
    if http_status == 429:
        return {
            "connected": True,
            "messages": [],
            "count": 0,
            "error": "Gmail rate limited. Try again shortly.",
            "google_status": http_status,
        }
    return {
        "connected": True,
        "messages": [],
        "count": 0,
        "error": message or "Could not load Gmail.",
        "google_status": http_status,
    }


def _parse_gmail_sender(raw: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)\s*<([^>]+)>", (raw or "").strip())
    if m:
        name = m.group(1).strip().strip('"')
        addr = m.group(2).strip()
        return name or addr, addr
    s = (raw or "").strip()
    return s, s


def _gmail_friendly_time(raw_date: str) -> str:
    try:
        dt = parsedate_to_datetime(raw_date)
        now = datetime.now(tz=timezone.utc)
        local_dt = dt.astimezone()
        if (now - dt).days == 0:
            return local_dt.strftime("%-I:%M %p")
        if (now - dt).days < 7:
            return local_dt.strftime("%a %d")
        return local_dt.strftime("%b %d")
    except Exception:
        return raw_date[:10] if raw_date else "—"


def _gmail_is_today(raw_date: str) -> bool:
    try:
        dt = parsedate_to_datetime(raw_date)
        now = datetime.now(tz=timezone.utc)
        return (now - dt).days == 0
    except Exception:
        return False


def _gmail_message_to_device_row(msg: dict) -> dict:
    """Shape one Gmail metadata row for device-ui / SPA inbox lists."""
    sender_name, sender_addr = _parse_gmail_sender(msg.get("from", ""))
    raw_date = msg.get("date", "")
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "sender": sender_name,
        "sender_email": sender_addr,
        "subject": msg.get("subject") or "(no subject)",
        "preview": msg.get("snippet", ""),
        "body": msg.get("snippet", ""),
        "time": _gmail_friendly_time(raw_date),
        "date": raw_date,
        "is_today": _gmail_is_today(raw_date),
        "is_read": msg.get("is_read", True),
        "to": "",
    }


@router.get("/integrations/calendar/events")
async def integrations_calendar_events(
    current_user: dict = Depends(get_current_user),
    days_past: int = Query(14, ge=0, le=90),
    days_future: int = Query(365, ge=1, le=547),
    max_results: int = Query(250, ge=1, le=500),
):
    """Upcoming/recent primary-calendar events using stored OAuth (read-only)."""
    from googleapiclient.errors import HttpError

    from services.calendar import list_events_time_range

    user_id = current_user["id"]
    creds = get_credentials_for_provider(user_id, "calendar")
    if not creds:
        return {"connected": False, "events": [], "count": 0}

    try:
        raw = list_events_time_range(
            creds,
            days_past=days_past,
            days_future=days_future,
            max_results=max_results,
        )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else 500
        reason = getattr(e, "reason", "") or ""
        msg = reason.strip() or "Google Calendar request failed."
        logger.warning("Calendar feed HttpError status=%s: %s", status, msg)
        return _calendar_feed_error_payload(status, msg)
    except Exception:
        logger.exception("Calendar feed unexpected error")
        return {
            "connected": True,
            "events": [],
            "count": 0,
            "error": "Could not load Google Calendar.",
            "google_status": None,
        }

    slim = []
    for ev in raw:
        org = ev.get("organizer") or {}
        slim.append({
            "id": ev.get("id"),
            "summary": ev.get("summary") or "(No title)",
            "start": ev.get("start") or {},
            "end": ev.get("end") or {},
            "htmlLink": ev.get("htmlLink"),
            "location": ev.get("location") or "",
            "description": (ev.get("description") or "").strip(),
            "organizer": (org.get("displayName") or org.get("email") or "").strip(),
            "hangoutLink": (ev.get("hangoutLink") or "").strip(),
            "reminders": ev.get("reminders"),
            "eventType": ev.get("eventType"),
            "status": ev.get("status"),
        })

    return {"connected": True, "events": slim, "count": len(slim)}


@router.get("/integrations/gmail/recent")
def integrations_gmail_recent(
    actor: dict = Depends(get_current_actor),
    max_results: int = Query(25, ge=1, le=50),
    days: int = Query(
        90,
        ge=1,
        le=730,
        description="Only mail from the last N days; forwarded to services.gmail.list_recent_messages.",
    ),
    q: str = Query("", max_length=500),
):
    """Recent Gmail message metadata via stored OAuth (read-only).

    Accepts JWT (dashboard) or paired-device token (device-ui); uses owner user_id.
    """
    from googleapiclient.errors import HttpError

    from services.gmail import list_recent_messages

    user_id = actor["user"]["id"]
    creds = get_credentials_for_provider(user_id, "gmail")
    if not creds:
        return {"connected": False, "messages": [], "count": 0}

    try:
        messages = list_recent_messages(
            creds,
            max_results=max_results,
            q=q or "",
            days=days,
        )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else 500
        reason = getattr(e, "reason", "") or ""
        msg = reason.strip() or "Gmail request failed."
        logger.warning("Gmail feed HttpError status=%s: %s", status, msg)
        return _gmail_feed_error_payload(status, msg)
    except Exception:
        logger.exception("Gmail feed unexpected error")
        return {
            "connected": True,
            "messages": [],
            "count": 0,
            "error": "Could not load Gmail.",
            "google_status": None,
        }

    return {"connected": True, "messages": messages, "count": len(messages)}


@router.get("/integrations/gmail/messages/{message_id}")
def integrations_gmail_message_detail(
    message_id: str,
    actor: dict = Depends(get_current_actor),
):
    """Full single-message fetch for device inbox detail (same Gmail OAuth as dashboard)."""
    from services.gmail import get_message_full

    user_id = actor["user"]["id"]
    creds = get_credentials_for_provider(user_id, "gmail")
    if not creds:
        raise HTTPException(status_code=403, detail="Gmail not connected.")

    try:
        msg = get_message_full(creds, message_id)
    except Exception as exc:
        logger.error("get_message_full %s failed: %s", message_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    sender_name, sender_addr = _parse_gmail_sender(msg.get("from", ""))
    raw_date = msg.get("date", "")
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "sender": sender_name,
        "sender_email": sender_addr,
        "subject": msg.get("subject", "(no subject)"),
        "preview": msg.get("snippet", ""),
        "body": msg.get("body", msg.get("snippet", "")),
        "time": _gmail_friendly_time(raw_date),
        "date": raw_date,
        "is_today": _gmail_is_today(raw_date),
        "is_read": msg.get("is_read", True),
        "to": (msg.get("to") or "").strip() or "—",
    }


@router.post("/integrations/gmail/messages/{message_id}/mark-unread")
def integrations_gmail_message_mark_unread(
    message_id: str,
    actor: dict = Depends(get_current_actor),
):
    from services.gmail import mark_message_unread

    user_id = actor["user"]["id"]
    creds = get_credentials_for_provider(user_id, "gmail")
    if not creds:
        raise HTTPException(status_code=403, detail="Gmail not connected.")
    try:
        mark_message_unread(creds, message_id)
    except Exception as exc:
        logger.error("mark_message_unread %s failed: %s", message_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", "id": message_id}


@router.post("/integrations/gmail/messages/{message_id}/mark-read")
def integrations_gmail_message_mark_read(
    message_id: str,
    actor: dict = Depends(get_current_actor),
):
    from services.gmail import mark_message_read

    user_id = actor["user"]["id"]
    creds = get_credentials_for_provider(user_id, "gmail")
    if not creds:
        raise HTTPException(status_code=403, detail="Gmail not connected.")
    try:
        mark_message_read(creds, message_id)
    except Exception as exc:
        logger.error("mark_message_read %s failed: %s", message_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", "id": message_id}


@router.post("/integrations/gmail/messages/{message_id}/archive")
def integrations_gmail_message_archive(
    message_id: str,
    actor: dict = Depends(get_current_actor),
):
    from services.gmail import archive_message

    user_id = actor["user"]["id"]
    creds = get_credentials_for_provider(user_id, "gmail")
    if not creds:
        raise HTTPException(status_code=403, detail="Gmail not connected.")
    try:
        archive_message(creds, message_id)
    except Exception as exc:
        logger.error("archive_message %s failed: %s", message_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", "id": message_id}


# ======================================================================
# OAUTH REDIRECT FLOW: STEP 1 -- Get authorization URL
# ======================================================================

@router.get("/integrations/{provider}/auth-url")
async def get_auth_url(
    provider: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Return the Google OAuth authorization URL for the given provider.
    The frontend should redirect the browser to this URL.
    """
    _check_google_configured()

    if provider not in SCOPES_BY_PROVIDER:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    state = _create_state_token(current_user["id"], provider)
    redirect_uri = _redirect_uri_for_request(request, provider)
    logger.info("OAuth auth-url provider=%s redirect_uri=%s", provider, redirect_uri)

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES_BY_PROVIDER[provider],
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return {"auth_url": auth_url}


# ======================================================================
# OAUTH REDIRECT FLOW: STEP 2 -- Handle callback from Google
# ======================================================================

@router.get("/integrations/{provider}/callback")
async def oauth_callback(
    request: Request,
    provider: str,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
):
    """
    Google redirects the browser here after the user authorizes (or denies).
    This endpoint exchanges the code for tokens, saves them, and redirects
    the browser back to the Settings page.
    """
    frontend_base = _post_oauth_browser_base(request)
    redirect_uri = _redirect_uri_for_request(request, provider)
    logger.info("OAuth callback provider=%s redirect_uri=%s", provider, redirect_uri)

    if error:
        logger.warning("OAuth callback error for %s: %s", provider, error)
        return RedirectResponse(
            url=f"{frontend_base}/settings?integration=error&reason={error}",
        )

    if not code or not state:
        return RedirectResponse(
            url=f"{frontend_base}/settings?integration=error&reason=missing_params",
        )

    payload = _verify_state_token(state)
    user_id = payload["sub"]

    if payload.get("provider") != provider:
        return RedirectResponse(
            url=f"{frontend_base}/settings?integration=error&reason=provider_mismatch",
        )

    scopes = SCOPES_BY_PROVIDER.get(provider, "")

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
    except Exception as e:
        logger.error("Token exchange failed (network): %s", e)
        return RedirectResponse(
            url=f"{frontend_base}/settings?integration=error&reason=network_error",
        )

    if resp.status_code != 200:
        try:
            err_data = resp.json()
            google_error = err_data.get("error", "unknown")
        except Exception:
            google_error = "unknown"
        logger.error("Token exchange failed: %s %s", resp.status_code, resp.text)
        return RedirectResponse(
            url=f"{frontend_base}/settings?integration=error&reason={google_error}",
        )

    token_data = resp.json()

    try:
        result = _save_tokens(user_id, provider, token_data, scopes)
        email = result.get("email", "")
    except Exception as e:
        logger.error("Failed to save tokens for %s: %s", provider, e)
        return RedirectResponse(
            url=f"{frontend_base}/settings?integration=error&reason=save_failed",
        )

    provider_name = "Gmail" if provider == "gmail" else "Google Calendar"
    return RedirectResponse(
        url=f"{frontend_base}/settings?integration=success&provider={quote_plus(provider_name)}&email={quote_plus(email)}",
    )


# ======================================================================
# DISCONNECT
# ======================================================================

@router.post("/integrations/{provider}/disconnect")
async def disconnect_integration(provider: str, current_user: dict = Depends(get_current_user)):
    """Remove stored OAuth tokens for an integration."""
    user_id = current_user["id"]
    integration = _get_integration(user_id, provider)
    if not integration:
        raise HTTPException(status_code=404, detail=f"{provider} is not connected")

    try:
        async with httpx.AsyncClient() as http:
            await http.post(
                REVOKE_URL,
                params={"token": integration["access_token"]},
                timeout=10,
            )
    except Exception:
        pass

    conn = get_connection()
    try:
        conn.execute("DELETE FROM integrations WHERE user_id = ? AND provider = ?", (user_id, provider))
        conn.commit()
    finally:
        conn.close()

    logger.info("Disconnected %s for user %s", provider, user_id)
    return {"status": "disconnected", "provider": provider}



