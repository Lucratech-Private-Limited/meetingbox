"""Tests for the desktop loopback Google sign-in helper (google_signin)."""

import urllib.parse
import urllib.request

import pytest

import google_signin


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_loopback_signin_returns_token(monkeypatch):
    """A simulated browser+backend redirect to the loopback URL yields the JWT."""
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["origin"] = (headers or {}).get("Origin", "")
        return _FakeResp({"auth_url": "https://accounts.google.com/o/oauth2/v2/auth?fake=1"})

    def fake_browser_open(url, **_kw):
        # Simulate Google -> backend -> 302 to our loopback listener with a token.
        cb = f"{captured['origin']}/auth/callback?token=test.jwt.value"
        urllib.request.urlopen(cb, timeout=5).read()
        return True

    monkeypatch.setattr(google_signin.httpx, "get", fake_get)
    monkeypatch.setattr(google_signin.webbrowser, "open", fake_browser_open)

    token = google_signin.sign_in_with_google("https://example.test", timeout=10)
    assert token == "test.jwt.value"
    assert captured["origin"].startswith("http://127.0.0.1:")


def test_loopback_signin_reports_oauth_error(monkeypatch):
    """An ``?error=`` redirect surfaces as a SignInError, not a hang."""

    def fake_get(url, headers=None, timeout=None):
        origin = (headers or {}).get("Origin", "")
        fake_get.origin = origin
        return _FakeResp({"auth_url": "https://accounts.google.com/o/oauth2/v2/auth?fake=1"})

    def fake_browser_open(url, **_kw):
        cb = f"{fake_get.origin}/auth/callback?error=access_denied"
        urllib.request.urlopen(cb, timeout=5).read()
        return True

    monkeypatch.setattr(google_signin.httpx, "get", fake_get)
    monkeypatch.setattr(google_signin.webbrowser, "open", fake_browser_open)

    with pytest.raises(google_signin.SignInError):
        google_signin.sign_in_with_google("https://example.test", timeout=10)


def test_loopback_signin_rejects_bad_auth_url(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _FakeResp({"auth_url": "not-a-url"})

    monkeypatch.setattr(google_signin.httpx, "get", fake_get)
    monkeypatch.setattr(google_signin.webbrowser, "open", lambda *a, **k: True)

    with pytest.raises(google_signin.SignInError):
        google_signin.sign_in_with_google("https://example.test", timeout=5)
