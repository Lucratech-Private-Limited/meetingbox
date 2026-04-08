"""
Local device user profiles (multi-user on one appliance).

Stored next to device settings JSON on the shared config volume.
Passwords are hashed with PBKDF2; plaintext is never written to disk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import BASE_DIR, resolve_device_config_dir

logger = logging.getLogger(__name__)


def profiles_file_path() -> Path:
    env = os.getenv("DEVICE_PROFILES_PATH", "").strip()
    if env:
        p = Path(env)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("DEVICE_PROFILES_PATH mkdir failed %s: %s", p, e)
        return p
    return resolve_device_config_dir() / "device_profiles.json"


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), 310_000
    )
    return f"pbkdf2_sha256$310000${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    try:
        algo, iterations, salt, hexhash = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        it = int(iterations)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("ascii"), it
        )
        return secrets.compare_digest(dk.hex(), hexhash)
    except (ValueError, AttributeError):
        return False


def _empty_store() -> Dict[str, Any]:
    return {
        "version": 1,
        "active_user_id": None,
        "profiles": [],
    }


def load_store() -> Dict[str, Any]:
    path = profiles_file_path()
    if not path.is_file():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_store()
        data.setdefault("version", 1)
        data.setdefault("active_user_id", None)
        data.setdefault("profiles", [])
        if not isinstance(data["profiles"], list):
            data["profiles"] = []
        return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not load profiles store %s: %s", path, e)
        return _empty_store()


def save_store(data: Dict[str, Any]) -> bool:
    path = profiles_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except OSError as e:
        logger.error("Could not save profiles store %s: %s", path, e)
        return False


def list_profiles(store: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    s = store if store is not None else load_store()
    out = []
    for p in s.get("profiles") or []:
        if isinstance(p, dict):
            out.append(
                {
                    "user_id": p.get("user_id", ""),
                    "display_name": p.get("display_name", ""),
                    "created_at": p.get("created_at", ""),
                }
            )
    return out


def find_profile(store: Dict[str, Any], user_id: str) -> Optional[Dict[str, Any]]:
    uid = (user_id or "").strip()
    for p in store.get("profiles") or []:
        if isinstance(p, dict) and (p.get("user_id") or "").strip() == uid:
            return p
    return None


def add_profile(user_id: str, display_name: str, password: str) -> tuple[bool, str]:
    uid = (user_id or "").strip()
    name = (display_name or "").strip()
    if not uid:
        return False, "User ID is required."
    if not name:
        return False, "Name is required."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."

    s = dict(load_store())
    s.setdefault("profiles", [])
    if find_profile(s, uid):
        return False, "That User ID is already taken. Choose another."

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {
        "user_id": uid,
        "display_name": name,
        "password_hash": _hash_password(password),
        "created_at": now,
    }
    s["profiles"] = list(s["profiles"]) + [entry]
    s["active_user_id"] = uid
    if not save_store(s):
        return False, "Could not save profile to disk."
    return True, ""


def set_active_user(user_id: str) -> bool:
    uid = (user_id or "").strip()
    s = load_store()
    if not find_profile(s, uid):
        return False
    s["active_user_id"] = uid
    return save_store(s)


def get_active_profile(store: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    s = store if store is not None else load_store()
    aid = (s.get("active_user_id") or "").strip()
    if not aid:
        return None
    return find_profile(s, aid)


def clear_active_profile_selection() -> None:
    """Clear active user on the device (e.g. after cloud unpair)."""
    s = load_store()
    s["active_user_id"] = None
    save_store(s)


def display_initials(display_name: str, max_len: int = 2) -> str:
    parts = (display_name or "").strip().split()
    if not parts:
        return "?"
    if len(parts) == 1:
        w = parts[0]
        return (w[:max_len] if len(w) >= 2 else w + "?")[:max_len].upper()
    return (parts[0][0] + parts[-1][0])[:max_len].upper()
