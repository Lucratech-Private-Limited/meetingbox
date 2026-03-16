"""
Google Integrations Routes -- Standard OAuth 2.0 Authorization Code flow
for Gmail and Google Calendar.

Uses the standard redirect-based OAuth flow: user clicks Connect, is redirected
to Google's consent screen, authorizes, and is redirected back to the app with
an authorization code that is exchanged for tokens.

Env vars required:
  GOOGLE_CLIENT_ID      -- from Google Cloud Console (Web application type)
  GOOGLE_CLIENT_SECRET  -- from Google Cloud Console
  APP_BASE_URL          -- base URL of the web backend (e.g. http://localhost:8000)
"""

import json
import logging
import os
import uuid
from datetime import datetime
from urllib.parse import urlencode, quote_plus

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from auth import get_current_user, SECRET_KEY
from database import get_connection

logger = logging.getLogger(__name__)

router = APIRouter()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", APP_BASE_URL).rstrip("/")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

SCOPES_BY_PROVIDER = {
    "gmail": " ".join([
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
    ]),
    "calendar": " ".join([
        "https://www.googleapis.com/auth/calendar.events",
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
    return f"{APP_BASE_URL}/api/integrations/{provider}/callback"


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
        except Exception as e:
            logger.error("Token refresh failed for %s/%s: %s", user_id, provider, e)
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
        results.append({
            "id": provider_id,
            "name": meta["name"],
            "icon": meta["icon"],
            "description": meta["description"],
            "connected": integration is not None,
            "email": integration["email"] if integration else None,
        })

    return results


# ======================================================================
# OAUTH REDIRECT FLOW: STEP 1 -- Get authorization URL
# ======================================================================

@router.get("/integrations/{provider}/auth-url")
async def get_auth_url(provider: str, current_user: dict = Depends(get_current_user)):
    """
    Return the Google OAuth authorization URL for the given provider.
    The frontend should redirect the browser to this URL.
    """
    _check_google_configured()

    if provider not in SCOPES_BY_PROVIDER:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    state = _create_state_token(current_user["id"], provider)

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _get_redirect_uri(provider),
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
    frontend_base = FRONTEND_BASE_URL

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

    redirect_uri = _get_redirect_uri(provider)
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



