"""
Authentication Routes -- Google sign-in for dashboard users.
"""

import logging
import os
import re
import secrets
import uuid
from datetime import datetime
from urllib.parse import quote_plus, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt as jose_jwt

from auth import (
    SECRET_KEY,
    count_users,
    create_access_token,
    get_current_user,
    get_user_by_email,
    get_user_by_google_sub,
    get_user_by_username,
    hash_password,
)
from database import get_connection

logger = logging.getLogger(__name__)

router = APIRouter()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
GOOGLE_LOGIN_SCOPES = "openid email profile"


def _check_google_configured() -> None:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars.",
        )


def _infer_backend_base_url(request: Request) -> str:
    raw_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").strip()
    if raw_host:
        host = raw_host.split(",")[0].strip()
        proto_raw = (request.headers.get("x-forwarded-proto") or "").strip().lower()
        proto = proto_raw.split(",")[0].strip() if proto_raw else (request.url.scheme or "http")
        return f"{proto}://{host}".rstrip("/")
    return APP_BASE_URL


def _infer_frontend_base_url(request: Request) -> str:
    origin = (request.headers.get("origin") or "").strip()
    if origin:
        return origin.rstrip("/")
    referer = (request.headers.get("referer") or "").strip()
    if "://" in referer:
        parts = referer.split("/", 3)
        if len(parts) >= 3:
            return f"{parts[0]}//{parts[2]}".rstrip("/")
    return FRONTEND_BASE_URL


def _google_redirect_uri(request: Request) -> str:
    return f"{_infer_backend_base_url(request)}/api/auth/google/callback"


def _state_token(frontend_base: str) -> str:
    return jose_jwt.encode(
        {"nonce": uuid.uuid4().hex, "frontend_base": frontend_base},
        SECRET_KEY,
        algorithm="HS256",
    )


def _decode_state_token(state: str) -> dict:
    try:
        return jose_jwt.decode(state, SECRET_KEY, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.") from exc


def _user_response(user: dict) -> dict:
    username = (user.get("username") or user.get("email") or "").strip()
    display_name = (user.get("display_name") or user.get("email") or username or "MeetingBox User").strip()
    return {
        "id": user["id"],
        "username": username,
        "email": user.get("email"),
        "display_name": display_name,
        "role": user.get("role", "user"),
        "onboarding_complete": bool(user.get("onboarding_complete", 0)),
        "avatar_url": user.get("avatar_url"),
    }


def _make_unique_username(email: str) -> str:
    preferred = (email or "").strip().lower()
    if preferred and not get_user_by_username(preferred):
        return preferred

    local_part = preferred.split("@", 1)[0] if "@" in preferred else preferred
    base = re.sub(r"[^a-z0-9_]+", "_", local_part).strip("_") or "user"
    candidate = base
    suffix = 1
    while get_user_by_username(candidate):
        suffix += 1
        candidate = f"{base}_{suffix}"
    return candidate


def _upsert_google_user(profile: dict) -> dict:
    google_sub = str(profile.get("id") or "").strip()
    email = str(profile.get("email") or "").strip().lower()
    if not google_sub or not email:
        raise HTTPException(status_code=400, detail="Google profile did not include a valid id/email.")

    existing = get_user_by_google_sub(google_sub) or get_user_by_email(email)
    now = datetime.utcnow().isoformat()

    conn = get_connection()
    conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
    try:
        cur = conn.cursor()
        if existing:
            cur.execute(
                """
                UPDATE users
                SET email = ?, google_sub = ?, auth_provider = 'google', display_name = ?, avatar_url = ?
                WHERE id = ?
                """,
                (
                    email,
                    google_sub,
                    (profile.get("name") or email).strip(),
                    profile.get("picture"),
                    existing["id"],
                ),
            )
            conn.commit()
            cur.execute("SELECT * FROM users WHERE id = ?", (existing["id"],))
            return cur.fetchone()

        role = "admin" if count_users() == 0 else "user"
        user_id = str(uuid.uuid4())
        username = _make_unique_username(email)
        cur.execute(
            """
            INSERT INTO users
              (id, username, password_hash, email, display_name, role, auth_provider, google_sub, avatar_url, onboarding_complete, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'google', ?, ?, 0, ?)
            """,
            (
                user_id,
                username,
                hash_password(secrets.token_urlsafe(32)),
                email,
                (profile.get("name") or email).strip(),
                role,
                google_sub,
                profile.get("picture"),
                now,
            ),
        )
        conn.commit()
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()


@router.get("/has-users")
async def has_users():
    return {"has_users": count_users() > 0}


@router.get("/google/auth-url")
async def google_auth_url(request: Request):
    _check_google_configured()
    frontend_base = _infer_frontend_base_url(request)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _google_redirect_uri(request),
        "response_type": "code",
        "scope": GOOGLE_LOGIN_SCOPES,
        "access_type": "offline",
        "prompt": "select_account",
        "state": _state_token(frontend_base),
    }
    return {"auth_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}"}


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
):
    frontend_base = FRONTEND_BASE_URL
    if state:
        try:
            frontend_base = _decode_state_token(state).get("frontend_base") or FRONTEND_BASE_URL
        except HTTPException:
            pass

    if error:
        return RedirectResponse(url=f"{frontend_base}/login?error={quote_plus(error)}")
    if not code or not state:
        return RedirectResponse(url=f"{frontend_base}/login?error=missing_params")

    _check_google_configured()
    redirect_uri = _google_redirect_uri(request)

    try:
        payload = _decode_state_token(state)
        frontend_base = payload.get("frontend_base") or frontend_base
        async with httpx.AsyncClient(timeout=20) as http:
            token_resp = await http.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            access_token = token_resp.json().get("access_token")
            if not access_token:
                raise HTTPException(status_code=400, detail="Google token response missing access token.")
            profile_resp = await http.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            profile_resp.raise_for_status()
            user = _upsert_google_user(profile_resp.json())
    except HTTPException as exc:
        return RedirectResponse(url=f"{frontend_base}/login?error={quote_plus(str(exc.detail))}")
    except Exception as exc:
        logger.exception("Google sign-in failed: %s", exc)
        return RedirectResponse(url=f"{frontend_base}/login?error=google_sign_in_failed")

    token = create_access_token({"sub": user["id"], "role": user["role"]})
    return RedirectResponse(url=f"{frontend_base}/auth/callback?token={quote_plus(token)}")


@router.post("/setup")
async def setup_not_supported():
    raise HTTPException(status_code=410, detail="MeetingBox now uses Google sign-in only.")


@router.post("/register")
async def register_not_supported():
    raise HTTPException(status_code=410, detail="MeetingBox now uses Google sign-in only.")


@router.post("/login")
async def login_not_supported():
    raise HTTPException(status_code=410, detail="MeetingBox now uses Google sign-in only.")


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return _user_response(current_user)


@router.post("/complete-onboarding")
async def complete_onboarding(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET onboarding_complete = 1 WHERE id = ?",
            (current_user["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}
