"""
Authentication utilities for dashboard users and paired devices.
"""

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext

from database import get_connection

logger = logging.getLogger(__name__)
_WEAK_DEFAULT_SECRET = "meetingbox-dev-secret-change-in-production"


def _load_secret_key() -> str:
    configured = os.getenv("JWT_SECRET_KEY", "").strip()
    if configured and configured != _WEAK_DEFAULT_SECRET:
        return configured

    # Never use a known static secret. Fall back to an ephemeral secret so
    # deployments without JWT_SECRET_KEY fail safe instead of being forgeable.
    ephemeral = secrets.token_urlsafe(48)
    if configured == _WEAK_DEFAULT_SECRET:
        logger.warning(
            "Weak JWT_SECRET_KEY value detected and ignored. "
            "Set a strong JWT_SECRET_KEY in the environment."
        )
    else:
        logger.warning(
            "JWT_SECRET_KEY is not set. Using an ephemeral in-memory key; "
            "existing tokens will be invalid after restart."
        )
    return ephemeral


SECRET_KEY = _load_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "720"))  # 30 days default

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def _dict_row_factory(c, r):
    return {col[0]: r[i] for i, col in enumerate(c.description)}


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_device_api_token() -> str:
    return "mbd_" + secrets.token_urlsafe(32)


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_user_by_username(username: str) -> Optional[dict]:
    conn = get_connection()
    conn.row_factory = _dict_row_factory
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = ?", (username,))
        return cur.fetchone()
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[dict]:
    conn = get_connection()
    conn.row_factory = _dict_row_factory
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE LOWER(COALESCE(email, '')) = LOWER(?)", (email.strip(),))
        return cur.fetchone()
    finally:
        conn.close()


def get_user_by_google_sub(google_sub: str) -> Optional[dict]:
    conn = get_connection()
    conn.row_factory = _dict_row_factory
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,))
        return cur.fetchone()
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> Optional[dict]:
    conn = get_connection()
    conn.row_factory = _dict_row_factory
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()


def get_device_by_token(token: str) -> Optional[dict]:
    conn = get_connection()
    conn.row_factory = _dict_row_factory
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT d.*, u.id AS owner_user_id, u.email AS owner_email, u.display_name AS owner_display_name
            FROM devices d
            JOIN users u ON u.id = d.user_id
            WHERE d.auth_token_hash = ? AND COALESCE(d.status, 'active') = 'active'
            """,
            (hash_api_token(token),),
        )
        return cur.fetchone()
    finally:
        conn.close()


def touch_device_last_seen(device_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE devices SET last_seen_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), device_id),
        )
        conn.commit()
    finally:
        conn.close()


def count_users() -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        return cur.fetchone()[0]
    finally:
        conn.close()


def _get_jwt_user(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if not user_id or payload.get("token_type") == "device":
            return None
    except JWTError:
        return None
    return get_user_by_id(user_id)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """FastAPI dependency -- extracts and validates JWT, returns user dict."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    user = _get_jwt_user(credentials.credentials)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """Like get_current_user but returns None instead of raising."""
    if credentials is None:
        return None
    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None


async def get_optional_actor(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """Return either {"type": "user"} or {"type": "device"} actor context."""
    if credentials is None:
        return None

    token = credentials.credentials
    user = _get_jwt_user(token)
    if user is not None:
        return {"type": "user", "user": user}

    device = get_device_by_token(token)
    if device is not None:
        touch_device_last_seen(device["id"])
        return {
            "type": "device",
            "device": device,
            "user": {
                "id": device["owner_user_id"],
                "email": device.get("owner_email"),
                "display_name": device.get("owner_display_name"),
            },
        }
    return None


async def get_current_actor(
    actor: Optional[dict] = Depends(get_optional_actor),
) -> dict:
    if actor is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return actor


async def get_current_device_row(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """
    Require a valid paired-device API token (Bearer mbd_…).
    Used for pairing checks and device-initiated unpair.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing device credentials",
        )
    device = get_device_by_token(credentials.credentials)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device not paired or access revoked",
        )
    touch_device_last_seen(device["id"])
    return device
