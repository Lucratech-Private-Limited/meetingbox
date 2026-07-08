"""Shared TLS trust configuration for outbound WebSocket connections.

`websockets.connect(wss://...)` with no ``ssl=`` argument builds its context from
``ssl.create_default_context()``, which on Windows/macOS loads the **OS system
trust store**. On machines with AV "HTTPS scanning" (e.g. Kaspersky) or a stale
root store, that path can reject a perfectly valid server cert with
``CERTIFICATE_VERIFY_FAILED: certificate has expired`` even though the real cert
is fine — which silently breaks realtime voice and the device-events stream.

`httpx` (our REST client) already verifies against the bundled **certifi** CA
bundle and works on those same machines. This module gives the WebSocket paths
the same certifi-backed trust so behavior is consistent and PC-independent.
"""

from __future__ import annotations

import logging
import ssl
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_CONTEXT: ssl.SSLContext | None = None
_CONTEXT_FAILED = False


def _build_context() -> ssl.SSLContext | None:
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
        logger.info("WebSocket TLS using certifi bundle: %s", certifi.where())
        return ctx
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(
            "Falling back to system trust for WebSocket TLS (certifi unavailable: %s)",
            e,
        )
        try:
            return ssl.create_default_context()
        except Exception:
            return None


def certifi_ssl_context() -> ssl.SSLContext | None:
    """Process-wide certifi-backed TLS context (built once)."""
    global _CONTEXT, _CONTEXT_FAILED
    if _CONTEXT is None and not _CONTEXT_FAILED:
        _CONTEXT = _build_context()
        if _CONTEXT is None:
            _CONTEXT_FAILED = True
    return _CONTEXT


def ws_ssl_context(url: str) -> ssl.SSLContext | None:
    """Return a certifi-backed context for ``wss://`` URLs, else ``None``.

    Plain ``ws://`` needs no TLS, so we return ``None`` (passing a context for a
    non-TLS URL makes ``websockets`` raise).
    """
    try:
        scheme = urlparse(url).scheme.lower()
    except Exception:
        scheme = ""
    if scheme != "wss":
        return None
    return certifi_ssl_context()
