"""Security / data hardening: WS token, system status auth, admin DB backup."""

from __future__ import annotations

import importlib
import sqlite3
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient


def _reload_app(tmp_path, monkeypatch, **extra_env):
    db = tmp_path / "sec.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")
    for k, v in extra_env.items():
        monkeypatch.setenv(k, v)
    import auth
    import database

    importlib.reload(database)
    database.init_database()
    importlib.reload(auth)
    import main

    importlib.reload(main)
    return main.app


def test_websocket_rejects_when_secret_set_wrong_token(tmp_path, monkeypatch):
    _reload_app(tmp_path, monkeypatch, MEETINGBOX_WS_SHARED_SECRET="correct-token")
    import main

    client = TestClient(main.app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=wrong"):
            pass


def test_websocket_accepts_matching_token(tmp_path, monkeypatch):
    _reload_app(tmp_path, monkeypatch, MEETINGBOX_WS_SHARED_SECRET="ok")
    import main

    client = TestClient(main.app)
    with client.websocket_connect("/ws?token=ok") as ws:
        ws.send_text("ping")
        assert "ack:ping" in ws.receive_text()


def test_websocket_rejects_when_require_auth_no_token(tmp_path, monkeypatch):
    _reload_app(
        tmp_path,
        monkeypatch,
        JWT_SECRET_KEY="test-ws-jwt-secret-key-32chars!",
        MEETINGBOX_WS_REQUIRE_AUTH="1",
    )
    import main

    client = TestClient(main.app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws"):
            pass


def test_websocket_accepts_jwt_in_query_when_require_auth(tmp_path, monkeypatch):
    uid = str(uuid.uuid4())
    db_path = tmp_path / "jwtws.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db_path))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-ws-jwt-secret-key-32chars!")
    monkeypatch.setenv("MEETINGBOX_WS_REQUIRE_AUTH", "1")
    import auth
    import database

    importlib.reload(database)
    database.init_database()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) VALUES (?,?,?,?,?,?)",
        (uid, "u1", "x", "u@test", "user", "2020-01-01"),
    )
    conn.commit()
    conn.close()
    importlib.reload(auth)
    import main

    importlib.reload(main)
    token = auth.create_access_token({"sub": uid})
    client = TestClient(main.app)
    with client.websocket_connect(f"/ws?access_token={token}") as ws:
        ws.send_text("ping")
        assert "ack:ping" in ws.receive_text()


def test_websocket_shared_secret_or_jwt_when_both_set(tmp_path, monkeypatch):
    uid = str(uuid.uuid4())
    db_path = tmp_path / "bothws.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db_path))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-ws-jwt-secret-key-32chars!")
    monkeypatch.setenv("MEETINGBOX_WS_SHARED_SECRET", "infra")
    monkeypatch.setenv("MEETINGBOX_WS_REQUIRE_AUTH", "1")
    import auth
    import database

    importlib.reload(database)
    database.init_database()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) VALUES (?,?,?,?,?,?)",
        (uid, "u1", "x", "u@test", "user", "2020-01-01"),
    )
    conn.commit()
    conn.close()
    importlib.reload(auth)
    import main

    importlib.reload(main)
    tok = auth.create_access_token({"sub": uid})
    client = TestClient(main.app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws"):
            pass
    with client.websocket_connect(f"/ws?token=wrong&access_token={tok}") as ws:
        ws.send_text("x")
        assert "ack:x" in ws.receive_text()
    with client.websocket_connect("/ws?token=infra") as ws:
        ws.send_text("y")
        assert "ack:y" in ws.receive_text()


def test_system_status_401_when_auth_required(tmp_path, monkeypatch):
    _reload_app(
        tmp_path,
        monkeypatch,
        MEETINGBOX_SYSTEM_STATUS_REQUIRE_AUTH="1",
    )
    import main

    client = TestClient(main.app)
    r = client.get("/api/system/status")
    assert r.status_code == 401


@pytest.fixture
def fresh_admin_db(tmp_path, monkeypatch):
    dbpath = tmp_path / "adm.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(dbpath))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")
    import auth
    import database
    import routes.admin_memory

    importlib.reload(database)
    database.init_database()
    importlib.reload(auth)
    importlib.reload(routes.admin_memory)
    import main

    importlib.reload(main)
    adm = str(uuid.uuid4())
    usr = str(uuid.uuid4())
    conn = sqlite3.connect(str(dbpath))
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) VALUES (?,?,?,?,?,?)",
        (adm, "a1", "x", "a@test", "admin", "2020-01-01"),
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) VALUES (?,?,?,?,?,?)",
        (usr, "u1", "x", "u@test", "user", "2020-01-01"),
    )
    conn.commit()
    conn.close()
    return main.app, adm, usr


def test_admin_backup_creates_file(fresh_admin_db, tmp_path, monkeypatch):
    app, admin_id, _usr = fresh_admin_db
    monkeypatch.setenv("MEETINGBOX_BACKUP_DIR", str(tmp_path / "backups"))
    from auth import get_current_admin

    app.dependency_overrides[get_current_admin] = lambda: {"id": admin_id, "role": "admin"}
    client = TestClient(app)
    r = client.post("/api/admin/backup/database")
    app.dependency_overrides.clear()
    assert r.status_code == 200
    body = r.json()
    assert body.get("bytes", 0) > 0
    assert Path(body["path"]).is_file()
