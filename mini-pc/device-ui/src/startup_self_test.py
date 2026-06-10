"""
Startup self-diagnostics for device UI (runs once after boot).

Checks server reachability, WebSocket URL, device API when paired, microphone capture,
offline voice (Vosk) model layout, optional Realtime session mint, and offline TTS hints.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from config import USE_MOCK_BACKEND, get_device_auth_token
from voice_assistant import VoiceAssistant
from mic_input_resolve import (
    capture_device_fallback_candidates,
    resolve_sounddevice_capture_device_index,
)

logger = logging.getLogger(__name__)


@dataclass
class SelfCheckResult:
    name: str
    ok: bool
    detail: str = ""

    def format_line(self) -> str:
        mark = "OK" if self.ok else "FAIL"
        if self.detail:
            return f"[{mark}] {self.name}: {self.detail}"
        return f"[{mark}] {self.name}"


def _env_skip_realtime_mint() -> bool:
    v = (os.getenv("MEETINGBOX_STARTUP_SELF_TEST_SKIP_REALTIME") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _coerce_bool_settings(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def _mic_probe_blocking() -> tuple[bool, str]:
    """Open default or configured input device briefly; must run off the voice thread."""
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as e:
        return False, f"missing sounddevice/numpy ({e})"

    preferred_device_id = resolve_sounddevice_capture_device_index(sd)
    candidate_device_ids = capture_device_fallback_candidates(sd, preferred_device_id)

    def _rates(device_id):
        out: list[int] = []
        idx = device_id
        try:
            if idx is None:
                inp_def = sd.default.device[0]
                if isinstance(inp_def, int) and inp_def >= 0:
                    idx = inp_def
            if idx is not None:
                info = sd.query_devices(idx)
                dflt = int(float(info.get("default_samplerate") or 0))
                if dflt > 0:
                    out.append(dflt)
        except Exception:
            logger.debug("startup mic: default samplerate probe skipped", exc_info=True)
        for sr in (48000, 44100, 32000, 22050, 16000):
            if sr not in out:
                out.append(sr)
        return out

    last_err: Exception | None = None
    for device_id in candidate_device_ids:
        for sr in _rates(device_id):
            stream = None
            try:
                kwargs: dict = dict(
                    channels=1,
                    samplerate=sr,
                    blocksize=1024,
                    dtype="float32",
                )
                if device_id is not None:
                    kwargs["device"] = device_id
                stream = sd.InputStream(**kwargs)
                stream.start()
                data, _overflowed = stream.read(1024)
                if data is None or len(data) == 0:
                    raise RuntimeError("empty read")
                block = np.asarray(data, dtype=np.float64).reshape(-1)
                rms = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
                dev_note = f"device {device_id}" if device_id is not None else "default"
                return True, f"{dev_note}, {sr} Hz, rms={rms:.4f}"
            except Exception as e:
                last_err = e
            finally:
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
    return False, str(last_err or "microphone open failed")


async def _check_websocket(app: Any, on_status: Callable[[str], None]) -> SelfCheckResult:
    on_status("Testing WebSocket endpoint…")
    if USE_MOCK_BACKEND:
        return SelfCheckResult(
            "WebSocket",
            True,
            "skipped (MOCK_BACKEND — in-process stream)",
        )
    try:
        import websockets
        from api_client import build_websocket_url

        url = build_websocket_url(app.backend.ws_url)
        async with websockets.connect(
            url,
            open_timeout=12,
            ping_interval=None,
            close_timeout=2,
            max_size=None,
        ) as ws:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=6.0)
                if raw:
                    return SelfCheckResult("WebSocket", True, "connected, received handshake")
                return SelfCheckResult("WebSocket", True, "connected")
            except asyncio.TimeoutError:
                return SelfCheckResult(
                    "WebSocket",
                    True,
                    "connected (no message within timeout — acceptable)",
                )
    except Exception as e:
        logger.warning("startup WS check failed: %s", e)
        return SelfCheckResult("WebSocket", False, str(e))


async def _check_realtime_session(app: Any, voice_rt_setting: bool) -> SelfCheckResult:
    if USE_MOCK_BACKEND:
        try:
            data = await app.backend.create_realtime_voice_session()
            if isinstance(data, dict):
                return SelfCheckResult(
                    "Realtime voice (mint)",
                    True,
                    "mock client returned placeholder",
                )
        except Exception as e:
            return SelfCheckResult("Realtime voice (mint)", False, str(e))
        return SelfCheckResult("Realtime voice (mint)", False, "unexpected mock response")

    if _env_skip_realtime_mint():
        return SelfCheckResult(
            "Realtime voice (mint)",
            True,
            "skipped (MEETINGBOX_STARTUP_SELF_TEST_SKIP_REALTIME)",
        )

    tok = get_device_auth_token().strip()
    if not tok:
        return SelfCheckResult(
            "Realtime voice (mint)",
            True,
            "skipped — device not paired",
        )

    if not voice_rt_setting:
        return SelfCheckResult(
            "Realtime voice (mint)",
            True,
            "skipped — voice_realtime_assistant off in settings",
        )

    try:
        from realtime_voice_session import REALTIME_VOICE_IMPLEMENTED
    except ImportError:
        REALTIME_VOICE_IMPLEMENTED = False  # noqa: N806

    if not REALTIME_VOICE_IMPLEMENTED:
        return SelfCheckResult(
            "Realtime voice (mint)",
            True,
            "skipped — realtime module unavailable on device",
        )

    try:
        data = await app.backend.create_realtime_voice_session()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code if e.response is not None else "?"
        return SelfCheckResult(
            "Realtime voice (mint)",
            False,
            f"HTTP {code}",
        )
    except Exception as e:
        return SelfCheckResult("Realtime voice (mint)", False, str(e))

    if not isinstance(data, dict):
        return SelfCheckResult("Realtime voice (mint)", False, "invalid JSON body")

    secret = (data.get("client_secret") or "").strip()
    sess = data.get("session")
    if secret or isinstance(sess, dict):
        exp = data.get("expires_at")
        suffix = f" expires={exp}" if exp else ""
        return SelfCheckResult(
            "Realtime voice (mint)",
            True,
            f"session issued{suffix}".strip(),
        )
    return SelfCheckResult(
        "Realtime voice (mint)",
        False,
        "response missing client_secret/session",
    )


def _check_offline_tts() -> SelfCheckResult:
    env_model = (os.getenv("MEETINGBOX_PIPER_MODEL") or "").strip()
    if env_model:
        p = Path(env_model)
        if p.is_file():
            return SelfCheckResult("Offline TTS (Piper)", True, str(p))

    piper_bin = shutil.which("piper")
    candidates: list[str] = []
    for pattern in (
        "/usr/share/piper/voices/en_*-*.onnx",
        "/usr/local/share/piper/voices/en_*-*.onnx",
    ):
        candidates.extend(glob.glob(pattern))
    onnx = None
    for fp in sorted(set(os.path.normpath(x) for x in candidates if os.path.isfile(x))):
        bn = os.path.basename(fp).lower()
        if bn.startswith("en_us") or bn.startswith("en-us"):
            onnx = fp
            break
    if onnx is None:
        for fp in sorted(candidates):
            if os.path.isfile(fp):
                onnx = fp
                break

    if piper_bin and onnx:
        return SelfCheckResult(
            "Offline TTS",
            True,
            f"piper + model ({os.path.basename(onnx)})",
        )
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return SelfCheckResult(
            "Offline TTS",
            True,
            "espeak/espeak-ng available",
        )
    return SelfCheckResult(
        "Offline TTS",
        True,
        "no piper/espeak detected (cloud TTS may still work)",
    )


def _emit(
    results: list[SelfCheckResult],
    r: SelfCheckResult,
    on_log_line: Callable[[str], None] | None,
) -> None:
    results.append(r)
    if on_log_line is not None:
        on_log_line(r.format_line())


async def execute_startup_checks(
    app: Any,
    on_status: Callable[[str], None],
    on_log_line: Callable[[str], None] | None = None,
) -> list[SelfCheckResult]:
    results: list[SelfCheckResult] = []

    on_status("Testing server connection…")
    try:
        ok = await app.backend.health_check()
        _emit(
            results,
            SelfCheckResult(
                "Server connection",
                ok,
                "reachable" if ok else "health probes failed — check BACKEND_URL / nginx",
            ),
            on_log_line,
        )
    except Exception as e:
        _emit(results, SelfCheckResult("Server connection", False, str(e)), on_log_line)

    _emit(results, await _check_websocket(app, on_status), on_log_line)

    tok = get_device_auth_token().strip()
    voice_rt = _coerce_bool_settings(getattr(app, "voice_realtime_assistant", False))

    on_status("Testing device API…")
    if USE_MOCK_BACKEND:
        try:
            await app.backend.get_settings()
            _emit(
                results,
                SelfCheckResult("Device API", True, "mock settings OK"),
                on_log_line,
            )
        except Exception as e:
            _emit(results, SelfCheckResult("Device API", False, str(e)), on_log_line)
    elif not tok:
        _emit(
            results,
            SelfCheckResult(
                "Device API",
                True,
                "skipped — not paired yet",
            ),
            on_log_line,
        )
    else:
        try:
            s = await app.backend.get_settings()
            voice_rt = _coerce_bool_settings(s.get("voice_realtime_assistant", voice_rt))
            _emit(
                results,
                SelfCheckResult(
                    "Device API",
                    True,
                    f"paired; realtime_setting={voice_rt}",
                ),
                on_log_line,
            )
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else "?"
            _emit(
                results,
                SelfCheckResult(
                    "Device API",
                    False,
                    f"HTTP {code}",
                ),
                on_log_line,
            )
        except Exception as e:
            _emit(results, SelfCheckResult("Device API", False, str(e)), on_log_line)

    on_status("Testing microphone (voice assistant paused briefly)…")
    va = getattr(app, "voice_assistant", None)
    paused_here = False
    if va is not None:
        try:
            va.set_paused(True)
            paused_here = True
            await asyncio.sleep(0.4)
        except Exception as e:
            logger.debug("startup: voice pause failed: %s", e)

    try:
        mic_ok, mic_detail = await asyncio.to_thread(_mic_probe_blocking)
        _emit(
            results,
            SelfCheckResult(
                "Microphone",
                mic_ok,
                mic_detail,
            ),
            on_log_line,
        )
    except Exception as e:
        _emit(results, SelfCheckResult("Microphone", False, str(e)), on_log_line)
    finally:
        if va is not None and paused_here:
            try:
                va.set_paused(False)
            except Exception:
                logger.exception("startup: voice unpause failed")

    on_status("Checking offline speech model…")
    if va is None:
        _emit(
            results,
            SelfCheckResult("Vosk / wake model", False, "voice assistant missing"),
            on_log_line,
        )
    elif not va.available:
        _emit(
            results,
            SelfCheckResult(
                "Vosk / wake model",
                False,
                "sounddevice or vosk unavailable — wake word offline disabled",
            ),
            on_log_line,
        )
    elif VoiceAssistant._looks_like_model_dir(va.model_dir):
        _emit(
            results,
            SelfCheckResult(
                "Vosk / wake model",
                True,
                str(va.model_dir),
            ),
            on_log_line,
        )
    else:
        _emit(
            results,
            SelfCheckResult(
                "Vosk / wake model",
                False,
                f"directory missing or invalid: {va.model_dir}",
            ),
            on_log_line,
        )

    on_status("Checking offline speech output…")
    _emit(results, _check_offline_tts(), on_log_line)

    on_status("Realtime session (optional)…")
    _emit(results, await _check_realtime_session(app, voice_rt), on_log_line)

    on_status("Done.")
    return results
