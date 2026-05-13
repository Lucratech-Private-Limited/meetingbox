"""Assistant queue snapshot used by Morning Brief and device home-summary."""

from __future__ import annotations

import importlib
import json
import sqlite3
import uuid
from pathlib import Path

import pytest


def test_list_assistant_queue_for_briefing_shape(tmp_path, monkeypatch):
    db = tmp_path / "aq.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    import database

    importlib.reload(database)
    database.init_database()
    uid = str(uuid.uuid4())
    aid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) VALUES (?,?,?,?,?,?)",
        (uid, "u1", "x", "u@test", "user", "2020-01-01"),
    )
    conn.execute(
        """
        INSERT INTO assistant_audits
          (id, created_at, user_id, meeting_id, source, message, routed_agent_id, routing_method, response_json)
        VALUES (?, datetime('now'), ?, NULL, 'test', 'hi', 'calendar_agent', 'test', '{}')
        """,
        (aid, uid),
    )
    payload = {"title": "Sync", "start_time": "2026-05-11T10:00:00"}
    conn.execute(
        """
        INSERT INTO pending_assistant_actions
          (id, created_at, user_id, audit_id, agent_id, tool_name, payload, status)
        VALUES (?, datetime('now'), ?, ?, 'calendar_agent', 'calendar_create_event', ?, 'pending')
        """,
        (pid, uid, aid, json.dumps(payload)),
    )
    conn.commit()
    conn.close()

    import assistant_service as asvc

    importlib.reload(asvc)
    snap = asvc.list_assistant_queue_for_briefing(uid, limit=10)
    assert snap["count_pending"] == 1
    assert len(snap["items"]) == 1
    item = snap["items"][0]
    assert item["id"] == pid
    assert item["needs_approval"] is True
    assert "Sync" in item["brief_label"]


def test_approve_pending_marks_failed_on_unexpected_error(tmp_path, monkeypatch):
    db = tmp_path / "ap.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    import database

    importlib.reload(database)
    database.init_database()
    uid = str(uuid.uuid4())
    aid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) VALUES (?,?,?,?,?,?)",
        (uid, "u1", "x", "u@test", "user", "2020-01-01"),
    )
    conn.execute(
        """
        INSERT INTO assistant_audits
          (id, created_at, user_id, meeting_id, source, message, routed_agent_id, routing_method, response_json)
        VALUES (?, datetime('now'), ?, NULL, 'test', 'hi', 'calendar_agent', 'test', '{}')
        """,
        (aid, uid),
    )
    conn.execute(
        """
        INSERT INTO pending_assistant_actions
          (id, created_at, user_id, audit_id, agent_id, tool_name, payload, status)
        VALUES (?, datetime('now'), ?, ?, 'calendar_agent', 'calendar_create_event', '{}', 'pending')
        """,
        (pid, uid, aid),
    )
    conn.commit()
    conn.close()

    import assistant_service as asvc
    from fastapi import HTTPException

    importlib.reload(asvc)

    def _boom(_user_id: str, _payload: dict):
        raise RuntimeError("calendar backend exploded")

    monkeypatch.setattr(asvc, "calendar_create_from_payload", _boom)

    with pytest.raises(HTTPException) as ei:
        asvc.approve_pending_action(pid, uid)
    assert ei.value.status_code == 500

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT status, error FROM pending_assistant_actions WHERE id = ?",
        (pid,),
    ).fetchone()
    conn.close()
    assert row[0] == "failed"
    assert "exploded" in (row[1] or "")
