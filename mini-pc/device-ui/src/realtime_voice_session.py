"""
OpenAI Realtime voice bridge for the device UI.

Full duplex audio + WebSocket session can be expanded here. When Realtime is not
deployed, ``start()`` fails fast so the app can fall back to local Vosk commands.
"""

from __future__ import annotations

import logging
import threading

from kivy.clock import Clock

logger = logging.getLogger(__name__)


class RealtimeVoiceSession:
    """Minimal lifecycle wrapper — runs callbacks on a background thread."""

    def __init__(
        self,
        *,
        client_secret: str,
        model: str,
        backend_base_url: str,
        device_token: str,
        on_session_end,
        on_error,
        on_connected,
    ):
        self._client_secret = (client_secret or "").strip()
        self._model = (model or "").strip()
        self._backend_base_url = (backend_base_url or "").strip()
        self._device_token = (device_token or "").strip()
        self._on_session_end = on_session_end
        self._on_error = on_error
        self._on_connected = on_connected
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        def _run() -> None:
            try:
                # Marshaling: Kivy Clock / UI callbacks must run on the main thread.
                msg = "Realtime voice is not fully implemented in this build."

                def _emit_err(_dt):
                    try:
                        self._on_error(msg)
                    except Exception:
                        logger.exception("Realtime on_error callback failed")

                Clock.schedule_once(_emit_err, 0)
            except Exception:
                logger.exception("Realtime voice session failed")

                def _emit_fallback(_dt):
                    try:
                        self._on_error("Realtime voice failed unexpectedly.")
                    except Exception:
                        logger.exception("Realtime on_error callback failed")

                Clock.schedule_once(_emit_fallback, 0)

        self._thread = threading.Thread(target=_run, name="realtime-voice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Best-effort stop (stub has no long-lived socket yet)."""
        self._thread = None
