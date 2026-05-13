"""
Seed 5 random meetings per day for the next 10 days into Google Calendar.

TWO MODES:

  1. DB mode (--db-path / MEETINGBOX_DB_PATH):
       Reads stored OAuth tokens from the MeetingBox SQLite database.
       Requires GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET in the environment.

  2. Local OAuth mode (default when DB is unavailable):
       Opens a browser for a one-time Google sign-in, stores the token in
       ~/.meetingbox_calendar_token.json for reuse.
       Requires GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET in the environment.

Usage:
  python scripts/seed_calendar_meetings.py
  python scripts/seed_calendar_meetings.py --dry-run
  python scripts/seed_calendar_meetings.py --db-path ./data/transcripts/meetings.db
  python scripts/seed_calendar_meetings.py --timezone America/New_York --days 10 --per-day 5

Environment variables:
  GOOGLE_CLIENT_ID      (required)
  GOOGLE_CLIENT_SECRET  (required)
  MEETINGBOX_DB_PATH    (optional — path to SQLite DB for DB mode)
  CALENDAR_DEFAULT_TIMEZONE  (optional — IANA timezone, default Asia/Kolkata)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Meeting corpus
# ---------------------------------------------------------------------------

MEETING_TITLES = [
    "Sprint Planning",
    "Daily Standup",
    "Weekly Team Sync",
    "1:1 with Manager",
    "Product Roadmap Review",
    "Design Review",
    "Engineering All-Hands",
    "Customer Success Check-in",
    "Q2 OKR Review",
    "Backlog Grooming",
    "Architecture Decision",
    "Bug Triage",
    "Sales Pipeline Review",
    "Marketing Sync",
    "Onboarding Kickoff",
    "Retrospective",
    "Vendor Demo",
    "Investor Update",
    "Incident Post-Mortem",
    "Cross-functional Sync",
    "Partnership Discussion",
    "Finance Review",
    "UX Research Readout",
    "Release Coordination",
    "Security Review",
    "Data Review",
    "Hiring Panel Debrief",
    "Strategy Workshop",
    "Tech Debt Discussion",
    "Quarterly Business Review",
]

DURATIONS        = [15, 30, 45, 60, 90]
DURATION_WEIGHTS = [5, 40, 15, 30, 10]

LOCATIONS = [
    "Conference Room A",
    "Conference Room B",
    "Zoom",
    "Google Meet",
    "Teams",
    "Huddle Room",
    "Board Room",
    "", "", "",  # virtual / no location is common
]

WORK_HOURS_START = 9
WORK_HOURS_END   = 18

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TOKEN_FILE      = Path.home() / ".meetingbox_calendar_token.json"
TOKEN_URL       = "https://oauth2.googleapis.com/token"


# ---------------------------------------------------------------------------
# Random meeting generator
# ---------------------------------------------------------------------------

def _random_meetings_for_day(day: date, count: int, tz: ZoneInfo) -> list[dict]:
    titles = random.sample(MEETING_TITLES, min(count, len(MEETING_TITLES)))

    slots: list[int] = []
    for h in range(WORK_HOURS_START, WORK_HOURS_END):
        slots.append(h * 60)
        slots.append(h * 60 + 30)

    chosen = sorted(random.sample(slots, min(count, len(slots))))

    meetings = []
    for i, title in enumerate(titles):
        sm = chosen[i]
        start_dt = datetime(day.year, day.month, day.day,
                            sm // 60, sm % 60, 0, tzinfo=tz)
        duration = random.choices(DURATIONS, weights=DURATION_WEIGHTS, k=1)[0]
        meetings.append({
            "title":            title,
            "start_dt":         start_dt,
            "duration_minutes": duration,
            "location":         random.choice(LOCATIONS),
        })
    return meetings


# ---------------------------------------------------------------------------
# Credentials — DB mode
# ---------------------------------------------------------------------------

def _load_integration_from_db(db_path: str, user_email: str | None) -> dict | None:
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
    try:
        cur = conn.cursor()
        if user_email:
            cur.execute(
                """
                SELECT i.* FROM integrations i
                JOIN users u ON u.id = i.user_id
                WHERE i.provider = 'calendar'
                  AND LOWER(COALESCE(i.email, u.email, '')) = LOWER(?)
                LIMIT 1
                """,
                (user_email.strip(),),
            )
        else:
            cur.execute("SELECT * FROM integrations WHERE provider = 'calendar' LIMIT 1")
        return cur.fetchone()
    finally:
        conn.close()


def _update_db_tokens(db_path: str, integration_id: str, token: str, expiry: str | None):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE integrations SET access_token = ?, token_expiry = ? WHERE id = ?",
            (token, expiry, integration_id),
        )
        conn.commit()
    finally:
        conn.close()


def _creds_from_db(db_path: str, user_email: str | None, client_id: str, client_secret: str):
    row = _load_integration_from_db(db_path, user_email)
    if not row:
        filter_hint = f" for {user_email}" if user_email else ""
        print(f"[ERROR] No Google Calendar integration found in DB{filter_hint}.")
        return None

    print(f"[INFO] DB integration found for: {row.get('email', 'unknown')}")

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest

    expiry = None
    if row.get("token_expiry"):
        try:
            expiry = datetime.fromisoformat(row["token_expiry"])
        except (ValueError, TypeError):
            pass

    creds = Credentials(
        token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_uri=TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        expiry=expiry,
    )

    if creds.expired and creds.refresh_token:
        print("[INFO] Refreshing expired access token …")
        try:
            creds.refresh(GoogleRequest())
            _update_db_tokens(db_path, row["id"], creds.token,
                              creds.expiry.isoformat() if creds.expiry else None)
        except Exception as exc:
            print(f"[ERROR] Token refresh failed: {exc}")
            return None

    return creds


# ---------------------------------------------------------------------------
# Credentials — local OAuth mode (browser flow)
# ---------------------------------------------------------------------------

def _creds_from_local_oauth(client_id: str, client_secret: str):
    """Use cached token or launch browser OAuth flow."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest

    creds = None

    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            creds = Credentials(
                token=data.get("token"),
                refresh_token=data.get("refresh_token"),
                token_uri=TOKEN_URL,
                client_id=client_id,
                client_secret=client_secret,
                scopes=CALENDAR_SCOPES,
            )
            if data.get("expiry"):
                try:
                    creds.expiry = datetime.fromisoformat(data["expiry"])
                except (ValueError, TypeError):
                    pass
        except Exception:
            creds = None

    if creds and creds.expired and creds.refresh_token:
        print("[INFO] Refreshing cached token …")
        try:
            creds.refresh(GoogleRequest())
            _save_local_token(creds)
        except Exception as exc:
            print(f"[WARN] Refresh failed ({exc}), starting fresh OAuth flow.")
            creds = None

    if not creds or not creds.valid:
        creds = _run_browser_oauth(client_id, client_secret)
        if creds:
            _save_local_token(creds)

    return creds


def _run_browser_oauth(client_id: str, client_secret: str):
    """Launch the installed-app OAuth2 flow in the default browser."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("[ERROR] google-auth-oauthlib is not installed.")
        print("        Run: pip install google-auth-oauthlib")
        return None

    client_config = {
        "installed": {
            "client_id":     client_id,
            "client_secret": client_secret,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     TOKEN_URL,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }

    print("[INFO] Opening browser for Google Calendar authorization …")
    flow = InstalledAppFlow.from_client_config(client_config, CALENDAR_SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    print("[INFO] Authorization complete.")
    return creds


def _save_local_token(creds):
    try:
        TOKEN_FILE.write_text(json.dumps({
            "token":         creds.token,
            "refresh_token": creds.refresh_token,
            "expiry":        creds.expiry.isoformat() if creds.expiry else None,
        }))
        print(f"[INFO] Token cached at {TOKEN_FILE}")
    except Exception as exc:
        print(f"[WARN] Could not save token: {exc}")


# ---------------------------------------------------------------------------
# Calendar API
# ---------------------------------------------------------------------------

def _create_event(creds, title: str, start_dt: datetime, duration: int,
                  location: str, tz_name: str, dry_run: bool) -> str:
    end_dt = start_dt + timedelta(minutes=duration)

    if dry_run:
        loc_str = f"  @ {location}" if location else ""
        return (
            f"[DRY-RUN] {title!r:45s} "
            f"{start_dt.strftime('%a %Y-%m-%d %H:%M')}–{end_dt.strftime('%H:%M')} "
            f"({duration}m){loc_str}"
        )

    from googleapiclient.discovery import build

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    body = {
        "summary":  title,
        "location": location,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": tz_name},
    }
    result = service.events().insert(calendarId="primary", body=body).execute()
    return f"Created: {result.get('htmlLink')}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Seed 5 random meetings/day × 10 days into Google Calendar"
    )
    parser.add_argument("--db-path",    default=None,
                        help="Path to MeetingBox SQLite DB (skips local OAuth if found)")
    parser.add_argument("--user-email", default=None,
                        help="Google email to match in DB (DB mode only)")
    parser.add_argument("--timezone",   default=None,
                        help="IANA timezone, e.g. Asia/Kolkata (default: Asia/Kolkata)")
    parser.add_argument("--days",       type=int, default=10,
                        help="Number of days to seed (default 10)")
    parser.add_argument("--per-day",    type=int, default=5,
                        help="Meetings per day (default 5)")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Print events without creating them")
    args = parser.parse_args()

    # ---- resolve credentials ----
    client_id     = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print("[ERROR] GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set.")
        print("        Export them from your shell before running this script:")
        print("          $env:GOOGLE_CLIENT_ID='...'")
        print("          $env:GOOGLE_CLIENT_SECRET='...'")
        sys.exit(1)

    # Try DB mode first
    db_path = args.db_path or os.getenv("MEETINGBOX_DB_PATH", "")
    creds   = None

    if db_path and os.path.exists(db_path):
        print(f"[INFO] DB mode — using {db_path}")
        if not args.dry_run:
            creds = _creds_from_db(db_path, args.user_email, client_id, client_secret)
            if not creds:
                sys.exit(1)
    else:
        if db_path:
            print(f"[WARN] DB not found at {db_path!r} — falling back to local OAuth.")
        else:
            print("[INFO] No DB path — using local OAuth flow.")
        if not args.dry_run:
            creds = _creds_from_local_oauth(client_id, client_secret)
            if not creds:
                sys.exit(1)

    # ---- resolve timezone ----
    tz_name = (args.timezone or os.getenv("CALENDAR_DEFAULT_TIMEZONE") or "Asia/Kolkata").strip()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        print(f"[WARN] Unknown timezone {tz_name!r}, using Asia/Kolkata")
        tz_name, tz = "Asia/Kolkata", ZoneInfo("Asia/Kolkata")

    # ---- create events ----
    today = date.today()
    total = 0

    for offset in range(1, args.days + 1):
        target_day = today + timedelta(days=offset)
        meetings   = _random_meetings_for_day(target_day, args.per_day, tz)

        print(f"\n-- {target_day.strftime('%A, %d %b %Y')} --")
        for m in meetings:
            msg = _create_event(
                creds,
                title=m["title"],
                start_dt=m["start_dt"],
                duration=m["duration_minutes"],
                location=m["location"],
                tz_name=tz_name,
                dry_run=args.dry_run,
            )
            print(f"  {msg}")
            total += 1

    verb = "Would create" if args.dry_run else "Created"
    print(f"\n[DONE] {verb} {total} meetings across {args.days} days in {tz_name}.")


if __name__ == "__main__":
    main()
