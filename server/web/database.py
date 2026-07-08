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
              created_at TEXT,
              participants TEXT,
              recording_mode TEXT DEFAULT 'meeting'
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
            "ALTER TABLE meetings ADD COLUMN recording_mode TEXT DEFAULT 'meeting'",
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
            CREATE TABLE IF NOT EXISTS mem0_sqlite_ingest_log (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              ref_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              detail TEXT,
              FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_mem0_sqlite_ingest_user_created "
            "ON mem0_sqlite_ingest_log(user_id, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_mem0_sqlite_ingest_kind_ref "
            "ON mem0_sqlite_ingest_log(kind, ref_id)"
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS mem0_soft_deleted (
              id          TEXT PRIMARY KEY,
              memory_id   TEXT NOT NULL,
              user_id     TEXT NOT NULL,
              deleted_at  TEXT NOT NULL,
              deleted_by  TEXT,
              FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_mem0_soft_deleted_user "
            "ON mem0_soft_deleted(user_id)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_mem0_soft_deleted_mem_user "
            "ON mem0_soft_deleted(memory_id, user_id)"
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
            """
            CREATE TABLE IF NOT EXISTS known_contacts (
              user_id TEXT NOT NULL,
              email TEXT NOT NULL,
              name TEXT NOT NULL DEFAULT '',
              last_seen TEXT NOT NULL,
              interaction_count INTEGER NOT NULL DEFAULT 1,
              PRIMARY KEY (user_id, email),
              FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_known_contacts_user_name ON known_contacts(user_id, name)"
        )

        cursor.execute(
            "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("max_meeting_upload_seconds", "10800"),
        )

        # Idempotency table for background analysis jobs (Fix 9).
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_runs (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              job_type TEXT NOT NULL,
              run_date TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              result_summary TEXT,
              created_at TEXT NOT NULL,
              completed_at TEXT,
              FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_runs_user_job_date "
            "ON analysis_runs(user_id, job_type, run_date)"
        )

        # Personal notes — free-form note-taking per user.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_notes (
              id         TEXT PRIMARY KEY,
              user_id    TEXT NOT NULL,
              title      TEXT NOT NULL DEFAULT '',
              content    TEXT NOT NULL DEFAULT '',
              tags       TEXT DEFAULT '[]',
              pinned     INTEGER NOT NULL DEFAULT 0,
              source     TEXT NOT NULL DEFAULT 'manual',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_notes_user_updated "
            "ON user_notes(user_id, updated_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_notes_user_pinned "
            "ON user_notes(user_id, pinned)"
        )

        _init_recording_intelligence_schema(cursor)

        conn.commit()
    finally:
        conn.close()
    run_alembic_upgrade()


def _init_recording_intelligence_schema(cursor: "sqlite3.Cursor") -> None:
    """
    Rich, searchable metadata + semantic-search infrastructure for the
    Notes/Meetings retrieval system.

    Three stores, all keyed by ``meetings.id`` (the session id):

    * ``recording_context`` — the intent/context captured before and after a
      recording, plus entities (people, projects, events, …) and keywords
      extracted from the transcript. This is what makes a note about the
      "board meeting" findable even when those words never appear in the
      recorded audio.
    * ``recording_embeddings`` — one dense vector per recording (transcript +
      summary + metadata) for semantic similarity ranking. Vector is stored as
      raw float32 bytes so it works on a stock SQLite build.
    * ``recordings_fts`` — an FTS5 full-text index over the composed searchable
      text, used for fast, scalable keyword/transcript matching instead of
      ``LIKE '%word%'`` table scans.
    """
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS recording_context (
          meeting_id            TEXT PRIMARY KEY,
          session_type          TEXT DEFAULT 'meeting',
          session_intent        TEXT DEFAULT '',
          pre_context           TEXT DEFAULT '',
          post_context          TEXT DEFAULT '',
          intent_tags           TEXT DEFAULT '[]',
          context_tags          TEXT DEFAULT '[]',
          referenced_people     TEXT DEFAULT '[]',
          referenced_projects   TEXT DEFAULT '[]',
          referenced_events     TEXT DEFAULT '[]',
          referenced_organizations TEXT DEFAULT '[]',
          referenced_locations  TEXT DEFAULT '[]',
          referenced_topics     TEXT DEFAULT '[]',
          keywords              TEXT DEFAULT '[]',
          future_reference_tags TEXT DEFAULT '[]',
          search_blob           TEXT DEFAULT '',
          created_at            TEXT,
          updated_at            TEXT,
          FOREIGN KEY (meeting_id) REFERENCES meetings(id)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_recording_context_type "
        "ON recording_context(session_type)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS recording_embeddings (
          meeting_id TEXT PRIMARY KEY,
          model      TEXT NOT NULL,
          dim        INTEGER NOT NULL,
          vector     BLOB NOT NULL,
          updated_at TEXT,
          FOREIGN KEY (meeting_id) REFERENCES meetings(id)
        )
        """
    )

    # FTS5 may not be available on exotic SQLite builds; degrade gracefully.
    try:
        cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS recordings_fts USING fts5(
              meeting_id UNINDEXED,
              title,
              summary,
              transcript,
              metadata,
              tokenize = 'porter unicode61'
            )
            """
        )
    except sqlite3.OperationalError:
        import logging

        logging.getLogger("meetingbox.database").warning(
            "FTS5 not available; recording search will fall back to LIKE matching."
        )


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
