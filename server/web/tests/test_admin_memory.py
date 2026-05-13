"""Admin memory routes and Mem0 helpers (isolated DB via reload)."""

from __future__ import annotations

import importlib
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fresh_app(tmp_path, monkeypatch):
    """Fresh database and reloaded `main` app (avoids polluting dev DB_PATH)."""
    dbpath = tmp_path / "meetings.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(dbpath))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")
    import database

    importlib.reload(database)
    database.init_database()
    adm = str(uuid.uuid4())
    usr = str(uuid.uuid4())
    conn = sqlite3.connect(str(dbpath))
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (adm, "admin1", "x", "admin@test.dev", "admin", "2020-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (usr, "user1", "x", "user@test.dev", "user", "2020-01-01T00:00:00"),
    )
    conn.execute(
        """
        INSERT INTO meetings (id, user_id, device_id, title, start_time, end_time, duration, audio_path, status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (str(uuid.uuid4()), usr, None, "Test meet", None, None, None, None, "completed", "2024-06-01T10:00:00"),
    )
    conn.commit()
    conn.close()

    import main

    importlib.reload(main)
    from main import app

    yield app, adm, usr
    app.dependency_overrides.clear()


def test_search_memories_for_user_respects_disable(monkeypatch):
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")
    from services import mem0_service

    importlib.reload(mem0_service)
    out = mem0_service.search_memories_for_user("u1", "hello", top_k=3)
    assert out["hits"] == []
    assert out["mem0_enabled"] is False


def test_admin_meetings_summary_requires_admin_role(fresh_app):
    app, _adm, usr = fresh_app
    from auth import get_current_user

    def _user_only():
        return {"id": usr, "role": "user"}

    app.dependency_overrides[get_current_user] = _user_only
    client = TestClient(app)
    r = client.get(f"/api/admin/users/{usr}/meetings-summary")
    assert r.status_code == 403
    app.dependency_overrides.clear()


def test_admin_meetings_summary_ok_for_admin(fresh_app):
    app, adm, usr = fresh_app
    from auth import get_current_admin

    def _admin():
        return {"id": adm, "role": "admin"}

    app.dependency_overrides[get_current_admin] = _admin
    client = TestClient(app)
    r = client.get(f"/api/admin/users/{usr}/meetings-summary")
    assert r.status_code == 200
    data = r.json()
    assert data["user_id"] == usr
    assert data["meetings_total"] >= 1
    assert len(data["meetings"]) >= 1


def test_admin_memories_search_logs_access(fresh_app):
    app, adm, usr = fresh_app
    from auth import get_current_admin

    app.dependency_overrides[get_current_admin] = lambda: {"id": adm, "role": "admin"}
    client = TestClient(app)
    r = client.get(f"/api/admin/users/{usr}/memories?q=test")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == usr
    assert "hits" in body

    r2 = client.get("/api/admin/memory-access-log?limit=10")
    assert r2.status_code == 200
    entries = r2.json().get("entries") or []
    assert any(e.get("target_user_id") == usr and e.get("action") == "memories_search" for e in entries)
