"""Briefing and calendar week routes (auth + empty calendar)."""

from __future__ import annotations

import importlib
import sqlite3
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient


def _app_with_user(tmp_path, monkeypatch):
    db = tmp_path / "brief.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-brief-jwt-secret-min-32-chars!!")
    import auth
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
    importlib.reload(auth)
    import main

    importlib.reload(main)
    return main.app, uid


def test_calendar_week_requires_auth(tmp_path, monkeypatch):
    db = tmp_path / "brief2.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-brief-jwt-secret-min-32-chars!!")
    import database

    importlib.reload(database)
    database.init_database()
    import main

    importlib.reload(main)
    client = TestClient(main.app)
    res = client.get("/api/calendar/week?start=2026-05-04&end=2026-05-10")
    assert res.status_code == 401


def test_calendar_week_empty_days_when_no_google_creds(tmp_path, monkeypatch):
    app, uid = _app_with_user(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "routes.briefing.get_credentials_for_provider",
        lambda _user_id, _provider: None,
    )

    import auth

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    res = client.get(
        "/api/calendar/week?start=2026-05-04&end=2026-05-10",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert set(body["days"].keys()) == {
        "2026-05-04",
        "2026-05-05",
        "2026-05-06",
        "2026-05-07",
        "2026-05-08",
        "2026-05-09",
        "2026-05-10",
    }
    for _ds, payload in body["days"].items():
        assert payload["meetings"] == []


def test_briefing_context_ok_without_google(tmp_path, monkeypatch):
    app, uid = _app_with_user(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "routes.briefing.get_credentials_for_provider",
        lambda _user_id, _provider: None,
    )

    import auth

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    res = client.get(
        "/api/briefing/context",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert "days" in body
    assert body.get("calendar_connected") is False
    assert "pending_assistant" in body
    pa = body["pending_assistant"]
    assert "count_pending" in pa
    assert "items" in pa
