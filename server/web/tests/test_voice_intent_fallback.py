"""Voice Realtime: when STT/orchestrator miss, assistant_intent still routes to calendar or mail."""

from __future__ import annotations

import importlib
import sqlite3
import uuid

import orchestrator


def test_voice_realtime_fallback_to_calendar_agent(tmp_path, monkeypatch):
    db = tmp_path / "vif.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")

    import database

    importlib.reload(database)
    database.init_database()
    uid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) VALUES (?,?,?,?,?,?)",
        (uid, "u1", "x", "u@test", "user", "2020-01-01"),
    )
    conn.commit()
    conn.close()

    import assistant_service as asvc

    importlib.reload(asvc)

    monkeypatch.setattr(
        asvc,
        "route_intent",
        lambda text, user_id=None: orchestrator.RouteResult(
            agent_id=None, method="none", rationale="forced_miss"
        ),
    )
    monkeypatch.setattr(asvc, "plan_calendar_steps", lambda ctx: [])

    out = asvc.process_assistant_intent(
        message="do i have anything tomorrow",
        user_id=uid,
        meeting_id=None,
        source="voice_realtime",
    )
    assert out.get("routed_agent_id") == "calendar_agent"
    assert out.get("routing_method") == "voice_default"
    assert "not sure" not in (out.get("assistant_message") or "").lower()


def test_voice_realtime_fallback_to_communication_for_mail(tmp_path, monkeypatch):
    db = tmp_path / "vif2.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")

    import database

    importlib.reload(database)
    database.init_database()
    uid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) VALUES (?,?,?,?,?,?)",
        (uid, "u1", "x", "u@test", "user", "2020-01-01"),
    )
    conn.commit()
    conn.close()

    import assistant_service as asvc

    importlib.reload(asvc)

    monkeypatch.setattr(
        asvc,
        "route_intent",
        lambda text, user_id=None: orchestrator.RouteResult(
            agent_id=None, method="none", rationale="forced_miss"
        ),
    )
    monkeypatch.setattr(asvc, "plan_communication_steps", lambda ctx: [])

    out = asvc.process_assistant_intent(
        message="check my inbox briefly",
        user_id=uid,
        meeting_id=None,
        source="voice_realtime",
    )
    assert out.get("routed_agent_id") == "communication_agent"
    assert out.get("routing_method") == "voice_default"
