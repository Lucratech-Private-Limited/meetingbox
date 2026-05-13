"""WebSocket URL must include device token / shared secret for hardened API."""

import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import api_client  # noqa: E402


def test_build_websocket_url_adds_access_token(monkeypatch):
    monkeypatch.delenv("BACKEND_WS_SHARED_SECRET", raising=False)
    monkeypatch.delenv("MEETINGBOX_WS_SHARED_SECRET", raising=False)
    with mock.patch("api_client.get_device_auth_token", return_value="mbd_testtoken"):
        out = api_client.build_websocket_url("wss://api.example.com/ws")
    assert "access_token=mbd_testtoken" in out
    assert out.startswith("wss://api.example.com/ws")


def test_build_websocket_url_adds_shared_secret(monkeypatch):
    monkeypatch.setenv("MEETINGBOX_WS_SHARED_SECRET", "shh")
    with mock.patch("api_client.get_device_auth_token", return_value=""):
        out = api_client.build_websocket_url("ws://localhost:8000/ws")
    assert "token=shh" in out


def test_build_websocket_url_merges_existing_query(monkeypatch):
    monkeypatch.delenv("BACKEND_WS_SHARED_SECRET", raising=False)
    monkeypatch.delenv("MEETINGBOX_WS_SHARED_SECRET", raising=False)
    with mock.patch("api_client.get_device_auth_token", return_value="tok"):
        out = api_client.build_websocket_url("wss://h/ws?foo=1")
    assert "foo=1" in out
    assert "access_token=tok" in out
