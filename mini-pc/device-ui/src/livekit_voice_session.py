"""
LiveKit voice session client for the MeetingBox device UI.

A drop-in replacement for the old PipecatVoiceSession with the same public
interface so main.py only needs to swap the import / class name.

Architecture
------------
The full AI pipeline (STT → LLM → TTS) runs SERVER-SIDE via a LiveKit
Agents worker container. The device only:

  1. Calls the backend to mint a room JWT
        POST {backend_base_url}/api/voice/livekit/connect
        → { "url": "wss://...", "token": "...", "room": "voice-<uid>-<nonce>" }

  2. Joins the LiveKit room via the official Python SDK
       - publishes the microphone as an audio track,
       - subscribes to the assistant's audio track (the LiveKit Agents
         worker publishes it when it joins),
       - listens for JSON data-channel messages (state / navigate /
         interrupt / connected / error).

No OpenAI key is required on the device — the agent worker runs entirely
server-side. The legacy `openai_api_key` constructor kwarg is accepted but
ignored for backwards compatibility with main.py.
"""

from __future__ import annotations

import asyncio
import audioop
import json
import logging
import os
import queue
import socket as _socket
import subprocess
import sys
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

LIVEKIT_VOICE_IMPLEMENTED = True

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# 16 kHz is compatible with the OSM09 USB mic and most capture hardware.
# livekit-agents resamples to 24 kHz before feeding OpenAI Realtime.
_MIC_SAMPLE_RATE = 16000
_MIC_CHANNELS = 1
_MIC_CHUNK_MS = 20
_MIC_CHUNK_FRAMES = _MIC_SAMPLE_RATE * _MIC_CHUNK_MS // 1000
_MIC_CHUNK_BYTES = _MIC_CHUNK_FRAMES * 2 * _MIC_CHANNELS  # PCM16 bytes per chunk

# OpenAI Realtime outputs at 24 kHz mono PCM16. LiveKit's AudioStream can
# resample on read; we keep aplay at 24 kHz to match the previous behaviour.
_APLAY_RATE = 24000
_APLAY_CHANNELS = 1
_APLAY_FMT = "S16_LE"

_CONNECT_HTTP_TIMEOUT_S = 15.0
_STOP_TIMEOUT_S = 4.0

# Name of the sibling container that holds the OSM09 mic open via PyAudio.
# We stop it before capturing mic audio and restart it afterwards.
_AUDIO_CONTAINER = "meetingbox-appliance-audio"
_DOCKER_SOCK = "/var/run/docker.sock"


# ---------------------------------------------------------------------------
# Docker socket helpers (stop/start sibling container for mic access)
# ---------------------------------------------------------------------------


def _docker_post(path: str, timeout: float = 6.0) -> int:
    """POST to Docker socket REST API. Returns HTTP status code, or 0 on error."""
    try:
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(_DOCKER_SOCK)
        request = f"POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n"
        sock.sendall(request.encode())
        response = b""
        while True:
            chunk = sock.recv(1024)
            if not chunk:
                break
            response += chunk
            if b"\r\n\r\n" in response:
                break
        sock.close()
        status_line = response.split(b"\r\n")[0].decode(errors="replace")
        return int(status_line.split()[1]) if len(status_line.split()) >= 2 else 0
    except Exception as exc:
        logger.debug("Docker API POST %s failed: %s", path, exc)
        return 0


def _stop_audio_container() -> None:
    """Stop the audio capture container to free the USB mic for arecord."""
    if not os.path.exists(_DOCKER_SOCK):
        return
    status = _docker_post(f"/containers/{_AUDIO_CONTAINER}/stop?t=3")
    print(
        f"LIVEKIT_CLIENT audio container stop → HTTP {status}",
        file=sys.stderr, flush=True,
    )


def _start_audio_container() -> None:
    """Restart the audio capture container after releasing the mic."""
    if not os.path.exists(_DOCKER_SOCK):
        return
    status = _docker_post(f"/containers/{_AUDIO_CONTAINER}/start")
    print(
        f"LIVEKIT_CLIENT audio container start → HTTP {status}",
        file=sys.stderr, flush=True,
    )


# ---------------------------------------------------------------------------
# LiveKitVoiceSession
# ---------------------------------------------------------------------------


class LiveKitVoiceSession:
    """LiveKit-backed cloud voice client. Same surface as the prior
    `PipecatVoiceSession` so the device main.py can swap with a one-line
    import change."""

    def __init__(
        self,
        *,
        openai_api_key: str = "",     # ignored — pipeline runs server-side
        backend_base_url: str,
        device_token: str,
        on_session_end: Callable[[], None],
        on_error: Callable[[str], None],
        on_connected: Callable[[], None],
        on_device_navigate: Callable[[str], None],
        output_voice: Optional[str] = None,
        on_before_open_mic: Optional[Callable[[], None]] = None,
        on_state_change: Optional[Callable[[str], None]] = None,
    ) -> None:
        del openai_api_key  # unused — kept for API parity with the old class
        self._backend_base_url = backend_base_url.rstrip("/")
        self._device_token = device_token
        self._on_session_end = on_session_end
        self._on_error = on_error
        self._on_connected = on_connected
        self._on_device_navigate = on_device_navigate
        self._output_voice = output_voice or ""
        self._on_before_open_mic = on_before_open_mic
        self._on_state_change = on_state_change

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event = threading.Event()
        self._ended_unexpectedly = False
        # Shared dict with _run(); used by stop() to kill aplay even when
        # loop.stop() causes the coroutine's finally block to be skipped.
        self._aplay_state: dict = {"proc": None, "writer": None}

        # Mute mic uplink while the assistant TTS is playing back so we don't
        # transcribe our own speaker echo (matches the previous Pipecat path).
        self._mute_mic_uplink = False
        self._mute_until_monotonic = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_thread,
            name="livekit-voice-client",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=_STOP_TIMEOUT_S)
            self._thread = None
        # Safety net: if loop.stop() caused the coroutine's finally block to
        # be skipped, kill any lingering aplay process here.
        self._force_kill_aplay()

    def ended_unexpectedly(self) -> bool:
        return self._ended_unexpectedly

    def _force_kill_aplay(self) -> None:
        """Kill any lingering aplay process. Safe to call from any thread."""
        proc = self._aplay_state.get("proc")
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
            self._aplay_state["proc"] = None

    # ------------------------------------------------------------------
    # Thread entry
    # ------------------------------------------------------------------

    def _run_thread(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        except Exception:
            logger.exception("LiveKitVoiceSession thread error")
            self._ended_unexpectedly = True
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
        try:
            self._on_session_end()
        except Exception:
            logger.exception("on_session_end callback failed")

    # ------------------------------------------------------------------
    # Async session
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        # Connect the backend → mint LiveKit room JWT.
        try:
            url, token, room_name = await self._connect_backend()
        except Exception as exc:
            print(
                f"LIVEKIT_CLIENT connect_backend FAILED err={exc!r}",
                file=sys.stderr, flush=True,
            )
            logger.error("LiveKit connect failed: %s", exc)
            self._ended_unexpectedly = True
            try:
                self._on_error(str(exc))
            except Exception:
                pass
            return

        print(
            f"LIVEKIT_CLIENT got token room={room_name} url={url}",
            file=sys.stderr, flush=True,
        )

        # Release Vosk's hold on ALSA before we open the mic, so the
        # capture stream gets a clean handle.
        if self._on_before_open_mic:
            try:
                self._on_before_open_mic()
            except Exception:
                logger.exception("on_before_open_mic failed")

        # Lazy-imports so import-time failures (missing native libs on CI /
        # build hosts) don't blow up the device-ui module load.
        try:
            from livekit import rtc  # type: ignore
        except Exception as exc:
            print(
                f"LIVEKIT_CLIENT import livekit.rtc FAILED err={exc!r}",
                file=sys.stderr, flush=True,
            )
            logger.error("Cannot import livekit.rtc: %s", exc)
            self._ended_unexpectedly = True
            try:
                self._on_error(str(exc))
            except Exception:
                pass
            return

        room = rtc.Room()

        # ---- Subscribe to assistant audio + data messages ----

        aplay_q: queue.Queue = queue.Queue(maxsize=500)
        # Use the instance-level dict so stop()/_force_kill_aplay() can reach it.
        aplay_state = self._aplay_state

        def _spawn_aplay() -> subprocess.Popen:
            device_arg = os.getenv("MEETINGBOX_APLAY_DEVICE", "")
            cmd = [
                "aplay",
                "-r", str(_APLAY_RATE),
                "-f", _APLAY_FMT,
                "-c", str(_APLAY_CHANNELS),
                "-t", "raw",
            ]
            if device_arg:
                cmd += ["-D", device_arg]
            cmd.append("-")
            try:
                print(
                    f"LIVEKIT_CLIENT aplay spawn cmd={' '.join(cmd)}",
                    file=sys.stderr, flush=True,
                )
            except Exception:
                pass
            return subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
            )

        def _aplay_writer(proc: subprocess.Popen, q: queue.Queue) -> None:
            while True:
                chunk = q.get()
                if chunk is None:
                    break
                try:
                    if proc.stdin and proc.poll() is None:
                        proc.stdin.write(chunk)
                        proc.stdin.flush()
                except BrokenPipeError:
                    break
                except Exception:
                    logger.debug("aplay write error", exc_info=True)
                    break

        def _kill_aplay() -> None:
            proc = aplay_state.get("proc")
            writer = aplay_state.get("writer")
            if proc is not None:
                try:
                    aplay_q.put_nowait(None)
                except queue.Full:
                    pass
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass
                aplay_state["proc"] = None
            if writer is not None:
                writer.join(timeout=1)
                aplay_state["writer"] = None
            while not aplay_q.empty():
                try:
                    aplay_q.get_nowait()
                except queue.Empty:
                    break

        def _ensure_aplay() -> None:
            proc = aplay_state.get("proc")
            if proc is None or proc.poll() is not None:
                _kill_aplay()
                new_proc = _spawn_aplay()
                aplay_state["proc"] = new_proc
                writer = threading.Thread(
                    target=_aplay_writer, args=(new_proc, aplay_q), daemon=True,
                )
                writer.start()
                aplay_state["writer"] = writer

        async def _drain_audio_track(track) -> None:
            """Decode the assistant's audio track and feed PCM into aplay."""
            try:
                stream = rtc.AudioStream(
                    track,
                    sample_rate=_APLAY_RATE,
                    num_channels=_APLAY_CHANNELS,
                )
            except TypeError:
                # Older livekit-rtc didn't accept sample_rate kwarg — fall back
                # and resample manually below.
                stream = rtc.AudioStream(track)

            resample_state = None
            async for ev in stream:
                if self._stop_event.is_set():
                    break
                frame = getattr(ev, "frame", None)
                if frame is None:
                    continue
                pcm = bytes(frame.data)
                # Frame may be at the source rate; resample if not 24k.
                fr_rate = getattr(frame, "sample_rate", _APLAY_RATE)
                if fr_rate != _APLAY_RATE:
                    pcm, resample_state = audioop.ratecv(
                        pcm, 2, _APLAY_CHANNELS, fr_rate,
                        _APLAY_RATE, resample_state,
                    )
                # Extend the uplink mute window while assistant audio flows.
                self._mute_until_monotonic = max(
                    self._mute_until_monotonic, time.monotonic() + 0.9,
                )
                _ensure_aplay()
                try:
                    aplay_q.put_nowait(pcm)
                except queue.Full:
                    logger.debug("aplay queue full — dropping audio chunk")

        # Event handlers — LiveKit Python SDK fires callbacks on a dedicated
        # IO thread; defer all heavy work to the asyncio loop.
        loop = asyncio.get_running_loop()

        def _schedule(coro) -> None:
            try:
                loop.create_task(coro)
            except Exception:
                logger.debug("LK schedule failed", exc_info=True)

        @room.on("track_subscribed")
        def _on_track_subscribed(track, _publication, _participant) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                print(
                    f"LIVEKIT_CLIENT subscribed audio sid={track.sid}",
                    file=sys.stderr, flush=True,
                )
                _schedule(_drain_audio_track(track))

        @room.on("data_received")
        def _on_data(packet, *_args, **_kwargs) -> None:
            # Newer SDKs pass a DataPacket with .data; older positional args.
            try:
                raw = getattr(packet, "data", None)
                if raw is None and isinstance(packet, (bytes, bytearray)):
                    raw = bytes(packet)
                if raw is None:
                    return
                try:
                    event = json.loads(bytes(raw).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    logger.debug("Non-JSON data packet (%d bytes)", len(raw))
                    return
            except Exception:
                logger.debug("data packet decode failed", exc_info=True)
                return
            self._handle_event(event, kill_aplay=_kill_aplay)

        @room.on("disconnected")
        def _on_disconnected(_reason=None) -> None:
            print(
                f"LIVEKIT_CLIENT disconnected reason={_reason!r}",
                file=sys.stderr, flush=True,
            )
            self._stop_event.set()

        # ---- Connect ----------------------------------------------------

        try:
            print(
                f"LIVEKIT_CLIENT connecting to {url}",
                file=sys.stderr, flush=True,
            )
            await room.connect(url, token)
            print(
                "LIVEKIT_CLIENT room connected",
                file=sys.stderr, flush=True,
            )
        except Exception as exc:
            print(
                f"LIVEKIT_CLIENT room.connect FAILED err={exc!r}",
                file=sys.stderr, flush=True,
            )
            logger.error("LiveKit room.connect failed: %s", exc)
            self._ended_unexpectedly = True
            try:
                self._on_error(str(exc))
            except Exception:
                pass
            return

        try:
            self._on_connected()
        except Exception:
            logger.exception("on_connected callback failed")
        self._fire_state("connected")

        # ---- Publish mic ----------------------------------------------------

        try:
            await self._mic_loop(room, rtc)
        except Exception:
            logger.exception("LiveKit mic loop ended with error")
            self._ended_unexpectedly = True
        finally:
            _kill_aplay()
            try:
                await room.disconnect()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Backend bridge
    # ------------------------------------------------------------------

    async def _connect_backend(self) -> tuple[str, str, str]:
        """Mint a LiveKit room token via the backend.

        Returns (url, token, room_name). Falls back to LIVEKIT_URL env var
        when the backend response leaves `url` blank (useful in dev when the
        backend reuses an internal `ws://livekit:7880` URL not reachable
        from the device).
        """
        import httpx  # local import keeps cold-start small

        path = "/api/voice/livekit/connect"
        url = f"{self._backend_base_url}{path}"
        headers = {"Authorization": f"Bearer {self._device_token}"}

        async with httpx.AsyncClient(timeout=_CONNECT_HTTP_TIMEOUT_S) as cli:
            resp = await cli.post(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        lk_url = (data.get("url") or "").strip()
        token = (data.get("token") or "").strip()
        room_name = (data.get("room") or "").strip()
        if not token or not room_name:
            raise RuntimeError(f"Backend returned invalid LiveKit payload: {data!r}")
        if not lk_url:
            lk_url = (os.getenv("LIVEKIT_URL", "") or "").strip()
        if not lk_url:
            raise RuntimeError("LiveKit URL is not configured (neither backend nor LIVEKIT_URL).")

        return _normalize_livekit_url(lk_url), token, room_name

    # ------------------------------------------------------------------
    # Mic loop — device → LiveKit
    # ------------------------------------------------------------------

    async def _mic_loop(self, room, rtc) -> None:  # type: ignore[no-untyped-def]
        """Capture mic audio via arecord and publish it as a LiveKit audio track.

        Using arecord (ALSA directly) is more reliable than sounddevice/PortAudio
        on headless Linux — it avoids PortAudio ALSA init races and supports
        sharing the device with the audio-capture container via dsnoop.
        """
        # Resolve ALSA capture device. Prefer env override, then auto-detect,
        # then fall back to the ALSA 'default' virtual device (dsnoop-based,
        # works even when the hardware device is shared with another process).
        arecord_device = (os.getenv("MEETINGBOX_ARECORD_DEVICE", "") or "").strip()
        if not arecord_device:
            # Try to map AUDIO_INPUT_DEVICE_NAME to an ALSA hw: address.
            name_hint = (os.getenv("AUDIO_INPUT_DEVICE_NAME", "") or "").strip().lower()
            if name_hint:
                # OSM09 / "usb" → hw:1,0; built-in "alc" → hw:0,0
                if "usb" in name_hint or "osm" in name_hint:
                    arecord_device = "hw:1,0"
                elif "alc" in name_hint or "pcm" in name_hint:
                    arecord_device = "hw:0,0"
            if not arecord_device:
                arecord_device = "default"

        # LiveKit local audio source.
        audio_source = rtc.AudioSource(_MIC_SAMPLE_RATE, _MIC_CHANNELS)
        local_track = rtc.LocalAudioTrack.create_audio_track("mic", audio_source)
        try:
            opts = rtc.TrackPublishOptions()
            opts.source = rtc.TrackSource.SOURCE_MICROPHONE
        except Exception:
            opts = None

        if opts is not None:
            await room.local_participant.publish_track(local_track, opts)
        else:
            await room.local_participant.publish_track(local_track)

        # Release the mic from the audio-capture container so arecord can open it.
        _stop_audio_container()

        cmd = [
            "arecord",
            "-D", arecord_device,
            "-r", str(_MIC_SAMPLE_RATE),
            "-f", "S16_LE",
            "-c", str(_MIC_CHANNELS),
            "-t", "raw",
            "-",
        ]
        print(
            f"LIVEKIT_CLIENT mic arecord device={arecord_device} rate={_MIC_SAMPLE_RATE} "
            f"cmd={' '.join(cmd)}",
            file=sys.stderr, flush=True,
        )

        loop = asyncio.get_running_loop()
        mic_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        def _reader_thread() -> None:
            """Read raw PCM from arecord stdout and push chunks to mic_queue."""
            try:
                while True:
                    data = proc.stdout.read(_MIC_CHUNK_BYTES)
                    if not data:
                        break
                    try:
                        loop.call_soon_threadsafe(mic_queue.put_nowait, data)
                    except Exception:
                        pass
            except Exception:
                pass

        reader = threading.Thread(
            target=_reader_thread,
            name="livekit-arecord-reader",
            daemon=True,
        )
        reader.start()

        sent_frames = 0
        timeout_count = 0
        try:
            while not self._stop_event.is_set():
                try:
                    pcm = await asyncio.wait_for(mic_queue.get(), timeout=1.0)
                    timeout_count = 0
                except asyncio.TimeoutError:
                    timeout_count += 1
                    if timeout_count % 5 == 0:
                        print(
                            f"LIVEKIT_CLIENT mic timeout #{timeout_count} — "
                            f"no audio from arecord (device={arecord_device})",
                            file=sys.stderr, flush=True,
                        )
                    continue
                if self._mute_mic_uplink or time.monotonic() < self._mute_until_monotonic:
                    silence = b"\x00" * len(pcm)
                    frame = rtc.AudioFrame(
                        data=silence,
                        sample_rate=_MIC_SAMPLE_RATE,
                        num_channels=_MIC_CHANNELS,
                        samples_per_channel=len(silence) // (2 * _MIC_CHANNELS),
                    )
                    await audio_source.capture_frame(frame)
                    continue
                frame = rtc.AudioFrame(
                    data=pcm,
                    sample_rate=_MIC_SAMPLE_RATE,
                    num_channels=_MIC_CHANNELS,
                    samples_per_channel=len(pcm) // (2 * _MIC_CHANNELS),
                )
                await audio_source.capture_frame(frame)
                sent_frames += 1
                if sent_frames == 1:
                    print(
                        f"LIVEKIT_CLIENT first mic frame published (device={arecord_device})",
                        file=sys.stderr, flush=True,
                    )
                elif sent_frames % 250 == 0:
                    print(
                        f"LIVEKIT_CLIENT mic frames={sent_frames}",
                        file=sys.stderr, flush=True,
                    )
        except Exception as exc:
            print(
                f"LIVEKIT_CLIENT mic_loop EXCEPTION frames={sent_frames} err={exc!r}",
                file=sys.stderr, flush=True,
            )
            logger.error("Mic loop failed: %s", exc)
            try:
                self._on_error(f"Microphone error: {exc}")
            except Exception:
                pass
        finally:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
            reader.join(timeout=2)
            _start_audio_container()

    # ------------------------------------------------------------------
    # Data channel event dispatch
    # ------------------------------------------------------------------

    def _handle_event(self, event: dict, *, kill_aplay: Callable[[], None]) -> None:
        event_type = (event.get("type") or "").strip()

        if event_type == "state":
            st = (event.get("state") or "").strip().lower()
            self._fire_state(st)
            self._mute_mic_uplink = st == "speaking"
            if st == "speaking":
                self._mute_until_monotonic = max(
                    self._mute_until_monotonic, time.monotonic() + 0.9,
                )

        elif event_type == "navigate":
            screen = (event.get("screen") or "").strip()
            if screen:
                try:
                    self._on_device_navigate(screen)
                except Exception:
                    logger.exception("on_device_navigate(%r) failed", screen)

        elif event_type == "interrupt":
            kill_aplay()

        elif event_type == "connected":
            try:
                self._on_connected()
            except Exception:
                logger.exception("on_connected callback failed")

        elif event_type == "error":
            msg = str(event.get("message") or "").strip()
            logger.error("LiveKit server error: %s", msg)
            try:
                self._on_error(msg or "Unknown LiveKit error")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fire_state(self, state: str) -> None:
        if self._on_state_change and state:
            try:
                self._on_state_change(state)
            except Exception:
                logger.debug("on_state_change(%r) failed", state, exc_info=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_livekit_url(raw: str) -> str:
    """Accept https:// / http:// and rewrite to wss:// / ws:// so the LiveKit
    SDK is happy. Trim trailing path segments — LiveKit appends /rtc itself."""
    if not raw:
        return raw
    if raw.startswith(("ws://", "wss://")):
        return raw
    parsed = urlparse(raw)
    scheme = "wss" if parsed.scheme == "https" else (
        "ws" if parsed.scheme == "http" else parsed.scheme
    )
    return urlunparse((scheme, parsed.netloc, parsed.path, "", "", ""))
