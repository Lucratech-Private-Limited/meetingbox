"""User commitments SQLite + Mem0 ingest wiring."""

from __future__ import annotations

import importlib
import uuid

import pytest

from services import commitments_service
from services import mem0_service


def test_commitments_upsert_and_list(tmp_path, monkeypatch):
    db = tmp_path / "c.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    import database

    importlib.reload(database)
    database.init_database()
    uid = str(uuid.uuid4())
    conn = database.get_connection()
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) VALUES (?,?,?,?,?,?)",
        (uid, "u1", "x", "u@test", "user", "2020-01-01"),
    )
    conn.commit()
    conn.close()

    row = commitments_service.upsert_commitment(
        uid,
        {
            "title": "Meet design team next Friday",
            "detail": "User mentioned next Friday in chat",
            "tags": ["scheduling", "chat"],
            "source": "chat",
        },
    )
    assert row.get("id")
    assert row.get("status") == "active"

    listed = commitments_service.list_commitments_for_user(uid)
    assert len(listed) == 1
    assert listed[0]["title"] == "Meet design team next Friday"

    ctx = commitments_service.commitments_context_for_prompt(uid)
    assert "Meet design team" in ctx
    assert "scheduling" in ctx

    memo = commitments_service.format_commitment_for_mem0(row)
    assert "Meet design team" in memo
    assert "scheduling" in memo


def test_maybe_ingest_commitment_skips_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")
    importlib.reload(mem0_service)
    mem0_service.maybe_ingest_commitment_row(
        "user-1", {"id": "c1", "title": "t", "status": "active", "tags": "[]"}
    )
