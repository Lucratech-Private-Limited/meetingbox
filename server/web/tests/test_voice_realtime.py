"""Realtime voice API routes (session mint + tool invoke)."""

from __future__ import annotations

import importlib
import json
import sqlite3
import uuid
from unittest.mock import MagicMock

from starlette.testclient import TestClient


def _app_with_user(tmp_path, monkeypatch, **extra_env):
    db = tmp_path / "voice.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-voice-jwt-secret-min-32-chars!!!!")
    for k, v in extra_env.items():
        monkeypatch.setenv(k, v)
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


def test_realtime_session_503_when_disabled(tmp_path, monkeypatch):
    app, uid = _app_with_user(tmp_path, monkeypatch, MEETINGBOX_REALTIME_VOICE_ENABLED="0")
    import auth

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    res = client.post(
        "/api/voice/realtime/session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 503


def test_realtime_session_ok_with_mock_openai(tmp_path, monkeypatch):
    app, uid = _app_with_user(
        tmp_path,
        monkeypatch,
        MEETINGBOX_REALTIME_VOICE_ENABLED="1",
        OPENAI_API_KEY="sk-test",
    )
    import auth
    from routes import voice as voice_routes

    class _Sess:
        model = "gpt-realtime"

        def model_dump(self, mode=None):
            return {"model": self.model, "type": "realtime"}

    class _Created:
        value = "ek_test_secret"
        expires_at = 1_700_000_000
        session = _Sess()

    fake_cs = MagicMock()
    fake_cs.create.return_value = _Created()
    fake_rt = MagicMock()
    fake_rt.client_secrets = fake_cs
    fake_client = MagicMock()
    fake_client.realtime = fake_rt
    monkeypatch.setattr(voice_routes, "OpenAI", lambda **kwargs: fake_client)

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    res = client.post(
        "/api/voice/realtime/session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["client_secret"] == "ek_test_secret"
    assert body["model"] == "gpt-realtime"


def test_realtime_tool_invoke_memory_search(tmp_path, monkeypatch):
    app, uid = _app_with_user(
        tmp_path,
        monkeypatch,
        MEETINGBOX_REALTIME_VOICE_ENABLED="1",
    )
    import auth

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    res = client.post(
        "/api/voice/realtime/tools/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={"call_id": "call_1", "name": "memory_search", "arguments": '{"query":"notes"}'},
    )
    assert res.status_code == 200
    out = json.loads(res.json()["output"])
    assert out.get("mem0_enabled") is False


def test_realtime_tool_invoke_briefing_contains_keys(tmp_path, monkeypatch):
    app, uid = _app_with_user(
        tmp_path,
        monkeypatch,
        MEETINGBOX_REALTIME_VOICE_ENABLED="1",
    )
    import auth

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    res = client.post(
        "/api/voice/realtime/tools/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={"call_id": "call_2", "name": "get_briefing_context", "arguments": "{}"},
    )
    assert res.status_code == 200
    out = json.loads(res.json()["output"])
    for key in ("greeting", "days", "commitments", "pending_assistant", "mem0_snippet"):
        assert key in out
