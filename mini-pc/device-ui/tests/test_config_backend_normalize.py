"""BACKEND_URL / BACKEND_WS_URL normalization for cloud + Docker .env mistakes."""

import importlib
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _path_only(u: str) -> str:
    return (urlparse(u).path or "").rstrip("/")


def _reload_config(monkeypatch, **env):
    for key in ("BACKEND_URL", "BACKEND_WS_URL", "DASHBOARD_URL", "MOCK_BACKEND", "DEVICE_AUTH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import config

    return importlib.reload(config)


def test_backend_url_strips_trailing_api(monkeypatch):
    cfg = _reload_config(monkeypatch, BACKEND_URL="https://example.test/api")
    assert cfg.BACKEND_URL == "https://example.test"
    assert cfg.BACKEND_WS_URL == "wss://example.test/ws"


def test_backend_ws_url_fixes_api_ws_path(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        BACKEND_URL="https://example.test",
        BACKEND_WS_URL="wss://example.test/api/ws",
    )
    assert _path_only(cfg.BACKEND_WS_URL) == "/ws"


def test_clean_values_unchanged(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        BACKEND_URL="https://example.test",
        BACKEND_WS_URL="wss://example.test/ws",
    )
    assert cfg.BACKEND_URL == "https://example.test"
    assert cfg.BACKEND_WS_URL == "wss://example.test/ws"
