"""GET /api/commitments for signed-in user."""

from __future__ import annotations

import importlib
import uuid

import pytest
from starlette.testclient import TestClient


def _reload_app(tmp_path, monkeypatch):
    db = tmp_path / "commitments_api.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-commitments-tests-!")
    import auth
    import database

    importlib.reload(database)
    database.init_database()
    uid = str(uuid.uuid4())
    conn = database.get_connection()
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) VALUES (?,?,?,?,?,?)",
        (uid, "cu1", "x", "cu@test", "user", "2020-01-01"),
    )
    conn.commit()
    conn.close()
    importlib.reload(auth)
    import main

    importlib.reload(main)
    return main.app, uid


def test_commitments_requires_auth(tmp_path, monkeypatch):
    app, _ = _reload_app(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.get("/api/commitments")
    assert r.status_code == 401


def test_commitments_returns_list(tmp_path, monkeypatch):
    app, uid = _reload_app(tmp_path, monkeypatch)
    import auth

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    r = client.get("/api/commitments", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data.get("commitments") == []
    assert data.get("count") == 0


def test_commitments_returns_saved_row(tmp_path, monkeypatch):
    app, uid = _reload_app(tmp_path, monkeypatch)
    import auth
    from services import commitments_service

    commitments_service.upsert_commitment(uid, {"title": "Follow up QA", "detail": "", "tags": [], "source": "test"})

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    r = client.get("/api/commitments", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data.get("count") == 1
    assert data["commitments"][0].get("title") == "Follow up QA"
