"""
Desktop Google sign-in via the loopback (localhost) OAuth pattern.

The backend (`/api/auth/google/auth-url`) builds Google's authorize URL and
embeds a post-login redirect target taken from the request's ``Origin`` header
(see ``_infer_frontend_base_url`` server-side). Google itself only ever sees the
backend's registered callback (``{OAUTH_PUBLIC_BASE_URL}/api/auth/google/callback``),
so the final hop from the backend to ``http://127.0.0.1:<port>/auth/callback``
is invisible to Google and needs no Google Console / backend changes.

Flow:
  1. Start a one-shot HTTP listener on 127.0.0.1:<ephemeral-port>.
  2. GET /api/auth/google/auth-url with ``Origin: http://127.0.0.1:<port>``.
  3. Open the returned auth URL in the system browser.
  4. User signs in -> Google -> backend callback -> 302 to our listener with
     ``?token=<user JWT>``.
  5. Capture the token, return it.

This mirrors how CLIs such as ``gh`` and ``gcloud`` perform desktop OAuth.
"""

from __future__ import annotations

import http.server
import logging
import threading
import time
import urllib.parse
import webbrowser
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

# Time the user has to complete sign-in in the browser before we give up.
DEFAULT_TIMEOUT_SECONDS = 300.0
_AUTH_URL_TIMEOUT_SECONDS = 30.0
# Transient DNS/network blips (e.g. a flaky router DNS returning
# ``getaddrinfo failed``) shouldn't abort sign-in on the first try. Retry the
# initial auth-url fetch a few times with a short linear backoff.
_AUTH_URL_MAX_ATTEMPTS = 4
_AUTH_URL_RETRY_BACKOFF = 0.8  # seconds, multiplied by the attempt number
_CALLBACK_PATH = "/auth/callback"

_SUCCESS_HTML = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pepper AI</title>
<style>
  html,body{height:100%;margin:0}
  body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;
       background:#0b0d11;color:#fff;display:flex;align-items:center;justify-content:center}
  .card{text-align:center;padding:32px 40px}
  .tick{font-size:48px;color:#34c759;line-height:1}
  h1{font-size:22px;font-weight:600;margin:18px 0 8px}
  p{color:#9aa4b2;margin:0;font-size:15px}
</style></head>
<body><div class="card">
  <div class="tick">&#10003;</div>
  <h1>You're signed in</h1>
  <p>Return to the Pepper AI app &mdash; you can close this tab.</p>
</div></body></html>"""

_ERROR_HTML = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Pepper AI</title>
<style>body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;
background:#0b0d11;color:#fff;display:flex;height:100vh;align-items:center;
justify-content:center;margin:0}.card{text-align:center}p{color:#9aa4b2}</style>
</head><body><div class="card"><h1>Sign-in failed</h1>
<p>Return to the Pepper AI app and try again.</p></div></body></html>"""


class SignInError(Exception):
    """Raised when the desktop Google sign-in flow cannot complete."""


def sign_in_with_google(
    backend_url: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    on_auth_url: Optional[Callable[[str], None]] = None,
) -> str:
    """Run the loopback Google sign-in and return the user JWT.

    Blocking; call from a worker thread (e.g. ``asyncio.to_thread``).

    Args:
        backend_url: Base URL of the MeetingBox backend.
        timeout: Seconds to wait for the user to finish in the browser.
        on_auth_url: Optional callback invoked with the Google auth URL once it
            is known (useful to show a "didn't open? copy this link" fallback).

    Raises:
        SignInError: on any failure (network, timeout, user-denied, no token).
    """
    base = (backend_url or "").strip().rstrip("/")
    if not base:
        raise SignInError("No backend URL configured for sign-in.")

    result: dict[str, Optional[str]] = {"token": None, "error": None}
    done = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != _CALLBACK_PATH:
                # Ignore favicon and any stray requests.
                self.send_response(204)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            token = (qs.get("token") or [""])[0].strip()
            err = (qs.get("error") or [""])[0].strip()
            if token:
                result["token"] = token
                body, status = _SUCCESS_HTML, 200
            else:
                result["error"] = err or "missing_token"
                body, status = _ERROR_HTML, 400
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass
            done.set()

        def log_message(self, *_args):  # silence default stderr logging
            return

    # Bind to 127.0.0.1 (not "localhost"): on Windows "localhost" can resolve to
    # ::1 first, which would miss an IPv4-only listener. We also send the Origin
    # as 127.0.0.1 so the backend redirect target matches this socket exactly.
    try:
        httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    except OSError as exc:
        raise SignInError(f"Could not start local sign-in listener: {exc}") from exc

    port = httpd.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    server_thread = threading.Thread(
        target=httpd.serve_forever, name="google-signin-loopback", daemon=True
    )
    server_thread.start()

    try:
        auth_url = ""
        for attempt in range(1, _AUTH_URL_MAX_ATTEMPTS + 1):
            try:
                resp = httpx.get(
                    f"{base}/api/auth/google/auth-url",
                    headers={"Origin": origin, "Referer": origin + "/"},
                    timeout=_AUTH_URL_TIMEOUT_SECONDS,
                )
                resp.raise_for_status()
                auth_url = str((resp.json() or {}).get("auth_url", "")).strip()
                break
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
            ) as exc:
                # Transient connectivity (DNS refused/timed out, dropped
                # connection). Retry a few times before surfacing an error so a
                # single blip doesn't force the user to start over.
                logger.warning(
                    "auth-url fetch attempt %d/%d failed (transient): %s",
                    attempt, _AUTH_URL_MAX_ATTEMPTS, exc,
                )
                if attempt < _AUTH_URL_MAX_ATTEMPTS:
                    time.sleep(_AUTH_URL_RETRY_BACKOFF * attempt)
                    continue
                raise SignInError(
                    "Can't reach the sign-in server. Check your internet "
                    "connection and try again."
                ) from exc
            except httpx.HTTPError as exc:
                raise SignInError(f"Could not reach the sign-in server: {exc}") from exc
            except ValueError as exc:  # bad JSON
                raise SignInError("Sign-in server returned an invalid response.") from exc

        if not auth_url.lower().startswith(("http://", "https://")):
            raise SignInError("Sign-in server returned an invalid Google URL.")

        if on_auth_url is not None:
            try:
                on_auth_url(auth_url)
            except Exception:  # callback must never break the flow
                logger.debug("on_auth_url callback raised", exc_info=True)

        try:
            opened = webbrowser.open(auth_url, new=1, autoraise=True)
        except Exception:
            opened = False
        if not opened:
            logger.warning("Could not auto-open a browser for Google sign-in.")

        if not done.wait(timeout):
            raise SignInError("Timed out waiting for Google sign-in.")

        if result["error"]:
            raise SignInError(f"Google sign-in was not completed ({result['error']}).")
        token = (result["token"] or "").strip()
        if not token:
            raise SignInError("No sign-in token was received.")
        return token
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
