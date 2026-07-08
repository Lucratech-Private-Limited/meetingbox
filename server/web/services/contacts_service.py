"""Known contacts service.

Automatically learns email addresses from Gmail interactions (received emails,
sent emails, drafts) and from calendar attendees.  Provides a lookup that the
voice assistant and communication agent use when the user says "email vivek"
without spelling out an address.
"""

from __future__ import annotations

import email.utils
import logging
from datetime import datetime, timezone
from typing import Any

from database import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_address(raw: str) -> tuple[str, str]:
    """Parse an RFC 2822 address like 'John Doe <john@x.com>' → (name, email).

    Returns ('', '') when the input contains no valid email.
    """
    try:
        name, addr = email.utils.parseaddr(raw or "")
        addr = (addr or "").strip().lower()
        if addr and "@" in addr:
            return name.strip(), addr
    except Exception:
        pass
    # Last-resort: extract bare email with a simple check
    raw = (raw or "").strip().lower()
    if "@" in raw and " " not in raw:
        return "", raw
    return "", ""


def _parse_address_list(raw: str) -> list[tuple[str, str]]:
    """Parse a comma-separated list of RFC 2822 addresses."""
    results: list[tuple[str, str]] = []
    for part in (raw or "").split(","):
        name, addr = _parse_address(part.strip())
        if addr:
            results.append((name, addr))
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def store_contacts(user_id: str, contacts: list[dict[str, Any]]) -> None:
    """Upsert a list of ``{email, name}`` dicts into known_contacts.

    Silently skips invalid / missing addresses.  Thread-safe (SQLite
    serialised writes).
    """
    if not user_id or not contacts:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        for c in contacts:
            email_addr = (c.get("email") or "").strip().lower()
            if not email_addr or "@" not in email_addr:
                continue
            name = (c.get("name") or "").strip()
            conn.execute(
                """
                INSERT INTO known_contacts (user_id, email, name, last_seen, interaction_count)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(user_id, email) DO UPDATE SET
                    last_seen          = excluded.last_seen,
                    interaction_count  = interaction_count + 1,
                    name               = CASE WHEN excluded.name != ''
                                              THEN excluded.name
                                              ELSE name
                                         END
                """,
                (user_id, email_addr, name, now),
            )
        conn.commit()
    except Exception as exc:
        logger.warning("store_contacts failed for user %s: %s", user_id, exc)
    finally:
        conn.close()


def lookup_contacts(user_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search known_contacts by partial name or email.

    Returns a list of ``{email, name, interaction_count}`` dicts ordered by
    interaction frequency (most-frequent first).
    """
    if not user_id or not (query or "").strip():
        return []
    q = f"%{query.strip().lower()}%"
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT email, name, interaction_count
            FROM known_contacts
            WHERE user_id = ?
              AND (LOWER(email) LIKE ? OR LOWER(name) LIKE ?)
            ORDER BY interaction_count DESC
            LIMIT ?
            """,
            (user_id, q, q, limit),
        ).fetchall()
        return [
            {"email": row[0], "name": row[1] or "", "interaction_count": row[2]}
            for row in rows
        ]
    except Exception as exc:
        logger.warning("lookup_contacts failed for user %s: %s", user_id, exc)
        return []
    finally:
        conn.close()


def extract_and_store_from_gmail_messages(user_id: str, messages: list[dict]) -> None:
    """Extract sender addresses from ``list_recent_messages`` results and store.

    Called automatically after every ``gmail_list_recent`` so the address book
    self-populates without any manual user action.
    """
    contacts: list[dict[str, Any]] = []
    for m in messages:
        frm = (m.get("from") or "").strip()
        if frm:
            name, addr = _parse_address(frm)
            if addr:
                contacts.append({"email": addr, "name": name})
        # 'to' / 'cc' may be present on sent-mail queries
        for field in ("to", "cc"):
            raw = (m.get(field) or "").strip()
            if raw:
                for name, addr in _parse_address_list(raw):
                    contacts.append({"email": addr, "name": name})
    if contacts:
        store_contacts(user_id, contacts)


def store_contact_from_address_string(user_id: str, raw: str) -> None:
    """Store a single RFC 2822 address string (e.g. from a To/Cc field)."""
    contacts: list[dict[str, Any]] = []
    for name, addr in _parse_address_list(raw):
        contacts.append({"email": addr, "name": name})
    if contacts:
        store_contacts(user_id, contacts)


def harvest_from_gmail(
    user_id: str,
    credentials,
    query: str = "",
    max_messages: int = 200,
) -> int:
    """Learn lifetime contacts from the user's Gmail (sent + received).

    Scans message From/To/Cc headers for *query* (or broadly when empty),
    parses every correspondent address, and stores them under *user_id* —
    contacts are always user-scoped, so one user's contacts are never visible
    to another. The user's own address is skipped. Returns the number of
    address rows upserted.
    """
    if not user_id or credentials is None:
        return 0
    from services.gmail import get_self_email, harvest_contact_addresses

    try:
        self_email = get_self_email(credentials)
    except Exception:
        self_email = ""

    try:
        raw_fields = harvest_contact_addresses(
            credentials, query=query, max_messages=max_messages
        )
    except Exception as exc:
        logger.info("harvest_from_gmail fetch failed for %s: %s", user_id, exc)
        return 0

    contacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_fields:
        for name, addr in _parse_address_list(raw):
            if not addr or addr == self_email or addr in seen:
                continue
            seen.add(addr)
            contacts.append({"email": addr, "name": name})
    if contacts:
        store_contacts(user_id, contacts)
    return len(contacts)
