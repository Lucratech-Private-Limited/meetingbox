"""Startup device-token validation: valid / invalid / unknown classification.

Guards the behaviour that an expired or revoked Google sign-in (HTTP 401/403)
sends the user back through sign-in, while a transient network error never
discards a working token.
"""

import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import api_client  # noqa: E402


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


def _make_client():
    c = api_client.BackendClient(base_url="https://backend.example.com")
    return c


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_no_token_is_invalid():
    c = _make_client()
    with mock.patch("api_client.get_device_auth_token", return_value=""):
        assert _run(c.validate_device_token()) == "invalid"


def test_http_200_is_valid():
    c = _make_client()
    with mock.patch("api_client.get_device_auth_token", return_value="mbd_tok"):
        c.client.get = mock.AsyncMock(return_value=_FakeResp(200))
        assert _run(c.validate_device_token()) == "valid"


def test_http_401_is_invalid():
    c = _make_client()
    with mock.patch("api_client.get_device_auth_token", return_value="mbd_tok"):
        c.client.get = mock.AsyncMock(return_value=_FakeResp(401))
        assert _run(c.validate_device_token()) == "invalid"


def test_http_403_is_invalid():
    c = _make_client()
    with mock.patch("api_client.get_device_auth_token", return_value="mbd_tok"):
        c.client.get = mock.AsyncMock(return_value=_FakeResp(403))
        assert _run(c.validate_device_token()) == "invalid"


def test_network_error_is_unknown():
    c = _make_client()
    with mock.patch("api_client.get_device_auth_token", return_value="mbd_tok"):
        c.client.get = mock.AsyncMock(side_effect=ConnectionError("offline"))
        assert _run(c.validate_device_token()) == "unknown"


def test_http_500_is_unknown():
    c = _make_client()
    with mock.patch("api_client.get_device_auth_token", return_value="mbd_tok"):
        c.client.get = mock.AsyncMock(return_value=_FakeResp(500))
        assert _run(c.validate_device_token()) == "unknown"
