import os
import sqlite3
from pathlib import Path

DB_PATH = os.getenv("MEETINGBOX_DB_PATH", "/data/transcripts/meetings.db")


def init_database() -> None:
    """
    Initialize the core SQLite schema used by MeetingBox services.
    """
    db_dir = Path(DB_PATH).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS meetings (
              id TEXT PRIMARY KEY,
              title TEXT,
              start_time TEXT,
              end_time TEXT,
              duration INTEGER,
              audio_path TEXT,
              status TEXT,
              created_at TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS segments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              meeting_id TEXT,
              segment_num INTEGER,
              start_time REAL,
              end_time REAL,
              text TEXT,
              speaker_id TEXT,
              confidence REAL,
              FOREIGN KEY (meeting_id) REFERENCES meetings(id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS summaries (
              meeting_id TEXT PRIMARY KEY,
              summary TEXT,
              action_items TEXT,
              decisions TEXT,
              topics TEXT,
              sentiment TEXT,
              generated_at TEXT,
              FOREIGN KEY (meeting_id) REFERENCES meetings(id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS local_summaries (
              meeting_id TEXT PRIMARY KEY,
              summary TEXT,
              discussion_points TEXT,
              action_items TEXT,
              decisions TEXT,
              topics TEXT,
              sentiment TEXT,
              model_name TEXT,
              last_segment_num INTEGER DEFAULT -1,
              is_final INTEGER DEFAULT 0,
              generated_at TEXT,
              FOREIGN KEY (meeting_id) REFERENCES meetings(id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS processing_state (
              meeting_id TEXT PRIMARY KEY,
              last_enqueued_segment INTEGER DEFAULT -1,
              last_transcribed_segment INTEGER DEFAULT -1,
              last_summarized_segment INTEGER DEFAULT -1,
              recording_stopped INTEGER DEFAULT 0,
              updated_at TEXT,
              FOREIGN KEY (meeting_id) REFERENCES meetings(id)
            )
            """
        )

        try:
            cursor.execute("ALTER TABLE local_summaries ADD COLUMN discussion_points TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE local_summaries ADD COLUMN last_segment_num INTEGER DEFAULT -1")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE local_summaries ADD COLUMN is_final INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,
              username TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL,
              display_name TEXT,
              role TEXT DEFAULT 'user',
              onboarding_complete INTEGER DEFAULT 0,
              created_at TEXT
            )
            """
        )

        # Migration: add onboarding_complete to existing users tables
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN onboarding_complete INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Column already exists

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS actions (
              id TEXT PRIMARY KEY,
              meeting_id TEXT NOT NULL,
              type TEXT NOT NULL,
              kind TEXT,
              connector_target TEXT,
              execution_mode TEXT,
              title TEXT,
              description TEXT,
              assignee TEXT,
              confidence REAL,
              draft TEXT,
              payload TEXT,
              artifact TEXT,
              status TEXT DEFAULT 'pending',
              delivery_status TEXT,
              error TEXT,
              selected_at TEXT,
              executed_at TEXT,
              created_at TEXT,
              FOREIGN KEY (meeting_id) REFERENCES meetings(id)
            )
            """
        )

        for statement in [
            "ALTER TABLE actions ADD COLUMN kind TEXT",
            "ALTER TABLE actions ADD COLUMN connector_target TEXT",
            "ALTER TABLE actions ADD COLUMN execution_mode TEXT",
            "ALTER TABLE actions ADD COLUMN description TEXT",
            "ALTER TABLE actions ADD COLUMN payload TEXT",
            "ALTER TABLE actions ADD COLUMN artifact TEXT",
            "ALTER TABLE actions ADD COLUMN delivery_status TEXT",
            "ALTER TABLE actions ADD COLUMN error TEXT",
            "ALTER TABLE actions ADD COLUMN selected_at TEXT",
        ]:
            try:
                cursor.execute(statement)
            except sqlite3.OperationalError:
                pass

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS integrations (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              provider TEXT NOT NULL,
              scopes TEXT,
              access_token TEXT,
              refresh_token TEXT,
              token_expiry TEXT,
              email TEXT,
              connected_at TEXT,
              FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_integrations_user_provider ON integrations(user_id, provider)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_segments_meeting_id ON segments(meeting_id)")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_segments_meeting_segment_num ON segments(meeting_id, segment_num)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_meetings_created_at ON meetings(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_actions_meeting_id ON actions(meeting_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status)")

        conn.commit()
    finally:
        conn.close()


def get_connection() -> sqlite3.Connection:
    """Return a new SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
