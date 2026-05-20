"""Realtime voice API routes (session mint + tool invoke)."""

from __future__ import annotations

import importlib
import json
import sqlite3
import uuid
from datetime import datetime
from unittest.mock import MagicMock

from starlette.testclient import TestClient
from zoneinfo import ZoneInfo


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


def test_realtime_tool_invoke_memory_remember_returns_disabled_when_mem0_off(tmp_path, monkeypatch):
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
        json={
            "call_id": "call_mr",
            "name": "memory_remember",
            "arguments": '{"fact":"User prefers espresso in the morning only"}',
        },
    )
    assert res.status_code == 200
    out = json.loads(res.json()["output"])
    assert out.get("stored") is False
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


def test_realtime_tool_invoke_memory_remember_rejects_short_fact(tmp_path, monkeypatch):
    import services.mem0_service as m0

    monkeypatch.setattr(m0, "mem0_disabled_globally", lambda: False)
    monkeypatch.setattr(m0, "mem0_writes_disabled", lambda: False)

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
        json={
            "call_id": "call_mr2",
            "name": "memory_remember",
            "arguments": '{"fact":"tiny"}',
        },
    )
    assert res.status_code == 200
    out = json.loads(res.json()["output"])
    assert out.get("stored") is False
    assert out.get("error") == "too_short"
    assert out.get("mem0_enabled") is True


def test_realtime_get_briefing_default_days_covers_tomorrow(tmp_path, monkeypatch):
    """Empty tool args must use days_ahead=2 so 'meetings tomorrow' is not queried as today-only."""
    app, uid = _app_with_user(
        tmp_path,
        monkeypatch,
        MEETINGBOX_REALTIME_VOICE_ENABLED="1",
    )
    import auth
    import services.realtime_voice_tools as rv

    captured: dict = {}

    def _fake(**kwargs):
        captured["days_ahead"] = kwargs.get("days_ahead")
        return {
            "greeting": "x",
            "user_display_name": None,
            "timezone": "UTC",
            "today": "2099-01-01",
            "calendar_connected": False,
            "days": {},
            "commitments": [],
            "meetings_recent": [],
            "mem0_snippet": None,
            "pending_assistant": {"count": 0},
            "gmail_preview": {"connected": False},
        }

    monkeypatch.setattr(rv, "build_briefing_context_dict", _fake)

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    res = client.post(
        "/api/voice/realtime/tools/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={"call_id": "call_da", "name": "get_briefing_context", "arguments": "{}"},
    )
    assert res.status_code == 200
    assert captured.get("days_ahead") == 2


def test_realtime_tool_assistant_intent_mocked(tmp_path, monkeypatch):
    app, uid = _app_with_user(
        tmp_path,
        monkeypatch,
        MEETINGBOX_REALTIME_VOICE_ENABLED="1",
    )
    import auth
    import services.realtime_voice_tools as rv

    def _fake(**kwargs):
        return {
            "assistant_message": "Mock reply",
            "pending_actions": [{"id": "pa-1", "needs_approval": True}],
            "tool_results": [],
        }

    monkeypatch.setattr(rv, "process_assistant_intent", _fake)

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    res = client.post(
        "/api/voice/realtime/tools/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "call_id": "call_ai",
            "name": "assistant_intent",
            "arguments": '{"message":"schedule lunch tomorrow"}',
        },
    )
    assert res.status_code == 200
    out = json.loads(res.json()["output"])
    assert out["assistant_message"] == "Mock reply"
    assert out["pending_actions"][0]["id"] == "pa-1"


def test_realtime_tool_list_pending_empty(tmp_path, monkeypatch):
    app, uid = _app_with_user(
        tmp_path,
        monkeypatch,
        MEETINGBOX_REALTIME_VOICE_ENABLED="1",
    )
    import auth
    import services.realtime_voice_tools as rv

    monkeypatch.setattr(rv, "list_pending_actions_for_user", lambda u: [])
    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    res = client.post(
        "/api/voice/realtime/tools/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={"call_id": "call_lp", "name": "list_pending_actions", "arguments": "{}"},
    )
    assert res.status_code == 200
    out = json.loads(res.json()["output"])
    assert out["pending"] == []
    assert out["count"] == 0


def test_realtime_session_mock_receives_turn_detection_fields(tmp_path, monkeypatch):
    """client_secrets.create session dict includes VAD/audio fields forwarded to SDK."""
    app, uid = _app_with_user(
        tmp_path,
        monkeypatch,
        MEETINGBOX_REALTIME_VOICE_ENABLED="1",
        OPENAI_API_KEY="sk-test",
        OPENAI_REALTIME_VOICE="shimmer",
    )
    import auth
    from routes import voice as voice_routes

    captured: dict = {}

    class _Sess:
        model = "gpt-realtime-2"

        def model_dump(self, mode=None):
            return {"model": self.model, "type": "realtime"}

    class _Created:
        value = "ek_test_secret"
        expires_at = 1_700_000_000
        session = _Sess()

    def _capture_create(**kw):
        nonlocal captured
        sess = kw.get("session") or {}
        captured = dict(sess)
        return _Created()

    fake_cs = MagicMock()
    fake_cs.create = _capture_create
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
    audio = captured.get("audio") or {}
    inp = audio.get("input") or {}
    outp = audio.get("output") or {}
    td = inp.get("turn_detection") or {}
    assert td.get("type") == "semantic_vad"
    assert td.get("eagerness") == "high"
    assert td.get("create_response") is True
    assert td.get("interrupt_response") is False
    nr = inp.get("noise_reduction") or {}
    assert nr.get("type") == "far_field"
    assert outp.get("voice") == "shimmer"
    assert "reasoning" in captured and captured["reasoning"]["effort"] == "minimal"


def test_realtime_session_coerces_translate_whisper_to_speech_model(tmp_path, monkeypatch):
    """Wrong Realtime SKU (translate / STT) must not reach OpenAI session create."""
    app, uid = _app_with_user(
        tmp_path,
        monkeypatch,
        MEETINGBOX_REALTIME_VOICE_ENABLED="1",
        OPENAI_API_KEY="sk-test",
        OPENAI_REALTIME_MODEL="gpt-realtime-translate",
    )
    import auth
    from routes import voice as voice_routes

    captured: dict = {}

    class _Sess:
        model = "gpt-realtime-2"

        def model_dump(self, mode=None):
            return {"model": self.model, "type": "realtime"}

    class _Created:
        value = "ek_test_secret"
        expires_at = 1_700_000_000
        session = _Sess()

    def _capture_create(**kw):
        nonlocal captured
        sess = kw.get("session") or {}
        captured = dict(sess)
        return _Created()

    fake_cs = MagicMock()
    fake_cs.create = _capture_create
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
    assert captured.get("model") == "gpt-realtime-2"


def test_realtime_session_default_voice_is_marin(tmp_path, monkeypatch):
    """When OPENAI_REALTIME_VOICE / OPENAI_TTS_VOICE unset, default Realtime voice is marin."""
    app, uid = _app_with_user(
        tmp_path,
        monkeypatch,
        MEETINGBOX_REALTIME_VOICE_ENABLED="1",
        OPENAI_API_KEY="sk-test",
    )
    import auth
    from routes import voice as voice_routes

    captured: dict = {}

    class _Sess:
        model = "gpt-realtime-2"

        def model_dump(self, mode=None):
            return {"model": self.model, "type": "realtime"}

    class _Created:
        value = "ek_default_voice"
        expires_at = 1_700_000_000
        session = _Sess()

    def _capture_create(**kw):
        nonlocal captured
        sess = kw.get("session") or {}
        captured = dict(sess)
        return _Created()

    fake_cs = MagicMock()
    fake_cs.create = _capture_create
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
    audio = captured.get("audio") or {}
    outp = audio.get("output") or {}
    assert outp.get("voice") == "marin"


def test_navigate_device_ui_tool_returns_device_navigate(tmp_path, monkeypatch):
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
        json={
            "call_id": "nav1",
            "name": "navigate_device_ui",
            "arguments": '{"screen":"calendar"}',
        },
    )
    assert res.status_code == 200
    body = json.loads(res.json()["output"])
    assert body.get("ok") is True
    assert body.get("device_navigate") == "calendar"


def test_navigate_device_ui_maps_inbox_to_emails(tmp_path, monkeypatch):
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
        json={
            "call_id": "nav2",
            "name": "navigate_device_ui",
            "arguments": '{"screen":"inbox"}',
        },
    )
    assert res.status_code == 200
    body = json.loads(res.json()["output"])
    assert body.get("device_navigate") == "emails"


def test_e2e_realtime_get_briefing_user_asks_meetings_tomorrow_has_data(tmp_path, monkeypatch):
    """
    QA scenario: speaker asks about meetings tomorrow; Realtime omitted days_ahead.
    Default window must load today + tomorrow so a Google event on May 15 is present.
    """
    monkeypatch.setenv("CALENDAR_DEFAULT_TIMEZONE", "Asia/Kolkata")
    app, uid = _app_with_user(tmp_path, monkeypatch, MEETINGBOX_REALTIME_VOICE_ENABLED="1")

    import services.briefing_context as bc

    ist = ZoneInfo("Asia/Kolkata")
    frozen_local = datetime(2026, 5, 14, 18, 30, 0, tzinfo=ist)
    base_dt = bc.datetime

    class FrozenDatetime(base_dt):  # type: ignore[misc,valid-type]
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            if tz is None:
                return frozen_local
            return frozen_local.astimezone(tz)

    monkeypatch.setattr(bc, "datetime", FrozenDatetime)

    def fake_get_creds(_user_id: str, provider: str):
        return object() if provider == "calendar" else None

    monkeypatch.setattr(bc, "get_credentials_for_provider", fake_get_creds)

    def fake_list_events(_creds, _t_min, _t_max, max_results=200):  # noqa: ANN001
        assert 1 <= max_results <= 500
        return [
            {
                "id": "ev_standup",
                "summary": "Team standup tomorrow",
                "start": {
                    "dateTime": "2026-05-15T09:30:00+05:30",
                    "timeZone": "Asia/Kolkata",
                },
                "end": {
                    "dateTime": "2026-05-15T10:00:00+05:30",
                    "timeZone": "Asia/Kolkata",
                },
            }
        ]

    monkeypatch.setattr(bc, "list_events_in_range", fake_list_events)

    import auth

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)

    qa = client.post(
        "/api/voice/realtime/tools/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "call_id": "qa_tomorrow",
            "name": "get_briefing_context",
            "arguments": "{}",
        },
    )
    assert qa.status_code == 200
    payload = json.loads(qa.json()["output"])

    assert payload.get("timezone") == "Asia/Kolkata"
    assert payload.get("calendar_connected") is True
    assert payload.get("today") == "2026-05-14"
    days = payload.get("days") or {}

    keys = sorted(days.keys())
    assert keys == ["2026-05-14", "2026-05-15"], f"unexpected day keys — tester expects today+tomorrow: {keys}"

    row = days["2026-05-15"].get("meetings") or []
    titles = " ".join(m.get("title") or "" for m in row).lower()
    assert "team standup" in titles


def test_e2e_explicit_days_ahead_one_excludes_tomorrow(tmp_path, monkeypatch):
    """days_ahead=1 is intentionally today-only; tomorrow's calendar block must stay empty/out of range."""
    monkeypatch.setenv("CALENDAR_DEFAULT_TIMEZONE", "Asia/Kolkata")
    app, uid = _app_with_user(tmp_path, monkeypatch, MEETINGBOX_REALTIME_VOICE_ENABLED="1")

    import services.briefing_context as bc

    ist = ZoneInfo("Asia/Kolkata")
    frozen_local = datetime(2026, 5, 14, 18, 30, 0, tzinfo=ist)
    base_dt = bc.datetime

    class FrozenDatetime(base_dt):  # type: ignore[misc,valid-type]
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return frozen_local.astimezone(tz) if tz else frozen_local

    monkeypatch.setattr(bc, "datetime", FrozenDatetime)

    monkeypatch.setattr(
        bc,
        "get_credentials_for_provider",
        lambda _uid, provider: object() if provider == "calendar" else None,
    )

    monkeypatch.setattr(bc, "list_events_in_range", lambda *_a, **_k: [{"id": "x", "summary": "Future"}])

    import auth

    token = auth.create_access_token({"sub": uid})
    client = TestClient(app)
    res = client.post(
        "/api/voice/realtime/tools/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "call_id": "qa_one",
            "name": "get_briefing_context",
            "arguments": '{"days_ahead": 1}',
        },
    )
    assert res.status_code == 200
    out = json.loads(res.json()["output"])
    assert list((out.get("days") or {}).keys()) == ["2026-05-14"]
