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
              user_id TEXT,
              device_id TEXT,
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
        for statement in [
            "ALTER TABLE meetings ADD COLUMN user_id TEXT",
            "ALTER TABLE meetings ADD COLUMN device_id TEXT",
        ]:
            try:
                cursor.execute(statement)
            except sqlite3.OperationalError:
                pass

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,
              username TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL,
              email TEXT,
              display_name TEXT,
              role TEXT DEFAULT 'user',
              auth_provider TEXT DEFAULT 'local',
              google_sub TEXT,
              avatar_url TEXT,
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
        for statement in [
            "ALTER TABLE users ADD COLUMN email TEXT",
            "ALTER TABLE users ADD COLUMN auth_provider TEXT DEFAULT 'local'",
            "ALTER TABLE users ADD COLUMN google_sub TEXT",
            "ALTER TABLE users ADD COLUMN avatar_url TEXT",
        ]:
            try:
                cursor.execute(statement)
            except sqlite3.OperationalError:
                pass

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              device_name TEXT,
              serial_number TEXT,
              auth_token_hash TEXT,
              status TEXT DEFAULT 'active',
              paired_at TEXT,
              unpaired_at TEXT,
              last_seen_at TEXT,
              created_at TEXT,
              FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        for statement in [
            "ALTER TABLE devices ADD COLUMN serial_number TEXT",
            "ALTER TABLE devices ADD COLUMN auth_token_hash TEXT",
            "ALTER TABLE devices ADD COLUMN status TEXT DEFAULT 'active'",
            "ALTER TABLE devices ADD COLUMN paired_at TEXT",
            "ALTER TABLE devices ADD COLUMN unpaired_at TEXT",
            "ALTER TABLE devices ADD COLUMN last_seen_at TEXT",
        ]:
            try:
                cursor.execute(statement)
            except sqlite3.OperationalError:
                pass

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS device_pairing_codes (
              code TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              device_id TEXT,
              expires_at TEXT NOT NULL,
              claimed_at TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (user_id) REFERENCES users(id),
              FOREIGN KEY (device_id) REFERENCES devices(id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )

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
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users(email) WHERE email IS NOT NULL AND TRIM(email) != ''"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub_unique ON users(google_sub) WHERE google_sub IS NOT NULL AND TRIM(google_sub) != ''"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_meetings_user_id ON meetings(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_meetings_device_id ON meetings(device_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_devices_user_id ON devices(user_id)")
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_token_hash_unique ON devices(auth_token_hash) WHERE auth_token_hash IS NOT NULL AND TRIM(auth_token_hash) != ''"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pairing_codes_user_id ON device_pairing_codes(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pairing_codes_expires_at ON device_pairing_codes(expires_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_segments_meeting_id ON segments(meeting_id)")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_segments_meeting_segment_num ON segments(meeting_id, segment_num)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_meetings_created_at ON meetings(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_actions_meeting_id ON actions(meeting_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status)")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS assistant_audits (
              id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              user_id TEXT,
              meeting_id TEXT,
              source TEXT NOT NULL,
              message TEXT NOT NULL,
              routed_agent_id TEXT,
              routing_method TEXT,
              response_json TEXT,
              device_id TEXT,
              correlation_id TEXT,
              FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        for statement in [
            "ALTER TABLE assistant_audits ADD COLUMN device_id TEXT",
            "ALTER TABLE assistant_audits ADD COLUMN correlation_id TEXT",
        ]:
            try:
                cursor.execute(statement)
            except sqlite3.OperationalError:
                pass

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_assistant_actions (
              id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              user_id TEXT,
              audit_id TEXT NOT NULL,
              agent_id TEXT NOT NULL,
              tool_name TEXT NOT NULL,
              payload TEXT NOT NULL,
              status TEXT DEFAULT 'pending',
              result_json TEXT,
              error TEXT,
              resolved_at TEXT,
              FOREIGN KEY (user_id) REFERENCES users(id),
              FOREIGN KEY (audit_id) REFERENCES assistant_audits(id)
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_assistant_audits_created ON assistant_audits(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_assistant_audits_user ON assistant_audits(user_id)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_assistant_user_status ON pending_assistant_actions(user_id, status)"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_assistant_audit ON pending_assistant_actions(audit_id)")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_memory_access_log (
              id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              admin_user_id TEXT NOT NULL,
              target_user_id TEXT NOT NULL,
              action TEXT NOT NULL,
              detail TEXT,
              FOREIGN KEY (admin_user_id) REFERENCES users(id),
              FOREIGN KEY (target_user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_memory_log_created ON admin_memory_access_log(created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_memory_log_target ON admin_memory_access_log(target_user_id)"
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_commitments (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              title TEXT NOT NULL,
              detail TEXT,
              tags TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              remind_at TEXT,
              due_at TEXT,
              source TEXT,
              calendar_event_id TEXT,
              audit_id TEXT,
              meeting_id TEXT,
              mem0_synced INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_commitments_user_status ON user_commitments(user_id, status)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_commitments_remind ON user_commitments(user_id, remind_at)"
        )

        cursor.execute(
            "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("max_meeting_upload_seconds", "10800"),
        )

        conn.commit()
    finally:
        conn.close()
    run_alembic_upgrade()


def run_alembic_upgrade() -> None:
    """Apply SQL migrations after idempotent SQLite bootstrap."""
    try:
        from pathlib import Path

        from alembic import command
        from alembic.config import Config
    except ImportError:
        return
    ini = Path(__file__).resolve().parent / "alembic.ini"
    if not ini.exists():
        return
    cfg = Config(str(ini))
    try:
        command.upgrade(cfg, "head")
    except Exception:
        import logging

        logging.getLogger("meetingbox.database").warning(
            "Alembic upgrade failed (database may still be usable via init_database DDL).",
            exc_info=True,
        )


def get_connection() -> sqlite3.Connection:
    """Return a new SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
