"""Spawn ``mini-pc/audio/audio_capture.py`` as a child process of device-ui.

Enabled with ``MEETINGBOX_SPAWN_AUDIO=1`` (the Docker image sets this by
default in the merged image). This is the in-process alternative to running
``audio_capture.py`` as its own Docker / systemd service. Use only one at a
time ΓÇö two audio processes would compete for the mic and Redis channels.

Responsibilities:
* Locate ``audio_capture.py`` next to device-ui (Docker or native checkout).
* Spawn it with the UI's environment plus light defaults.
* Re-log child stdout/stderr through the UI logger as ``[audio] ...``.
* Restart the child with bounded exponential backoff if it dies while the UI is up.
* Terminate the child cleanly on ``stop()`` / shutdown.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("meetingbox.audio_supervisor")


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() not in ("", "0", "false", "no", "off")


def _resolve_audio_script() -> Optional[Path]:
    """Return absolute path to ``audio_capture.py``, or None when not found."""
    env_path = os.environ.get("MEETINGBOX_AUDIO_SCRIPT", "").strip()
    if env_path:
        p = Path(env_path).expanduser().resolve()
        return p if p.is_file() else None
    here = Path(__file__).resolve()
    # ``Path.parents[n]`` raises IndexError when n exceeds the number of
    # ancestors. In Docker /app/src has only 2 ancestors (/app and /), so
    # the old eager list construction crashed at parents[3] before the
    # valid candidate at parents[1] was checked.
    parents = here.parents
    layout_specs = (
        (1, ("audio",)),                   # Docker image: /app/src → /app/audio
        (2, ("audio",)),                   # Native checkout: device-ui/src → device-ui/audio
        (3, ("mini-pc", "audio")),         # Monorepo: meetingbox/mini-pc/.../src → meetingbox/mini-pc/audio
    )
    for parent_idx, suffix in layout_specs:
        if parent_idx >= len(parents):
            continue
        candidate = parents[parent_idx].joinpath(*suffix, "audio_capture.py")
        if candidate.is_file():
            return candidate
    return None


def _resolve_python() -> str:
    override = os.environ.get("MEETINGBOX_AUDIO_PYTHON", "").strip()
    if override:
        return override
    return sys.executable or "python3"


class AudioSupervisor:
    """Manage a child ``audio_capture.py`` process with auto-restart."""

    _BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)

    def __init__(self, audio_script: Path, python: str) -> None:
        self._script = audio_script
        self._python = python
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._supervise,
            name="MeetingBoxAudioSupervisor",
            daemon=True,
        )
        self._thread.start()
        logger.info("audio supervisor started (script=%s)", self._script)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._terminate_child(timeout=timeout)
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=max(1.0, timeout))
        self._thread = None
        logger.info("audio supervisor stopped")

    def _supervise(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self._spawn_once()
            except Exception:
                logger.exception("audio child raised; will retry")
            if self._stop.is_set():
                break
            backoff = self._BACKOFF_SECONDS[min(attempt, len(self._BACKOFF_SECONDS) - 1)]
            logger.warning("audio child exited; restarting in %.0fs", backoff)
            if self._stop.wait(backoff):
                break
            attempt += 1

    def _spawn_once(self) -> None:
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("REDIS_HOST", env.get("LOCAL_REDIS_HOST", "127.0.0.1"))
        env.setdefault("REDIS_PORT", env.get("LOCAL_REDIS_PORT", "6379"))

        cmd = [self._python, str(self._script)]
        cwd = str(self._script.parent)
        logger.info("spawning audio child: %s (cwd=%s)", " ".join(cmd), cwd)
        try:
            popen_kwargs: dict = {
                "cwd": cwd,
                "env": env,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
            }
            if sys.platform.startswith("linux"):
                popen_kwargs["start_new_session"] = True
            self._proc = subprocess.Popen(cmd, **popen_kwargs)
        except FileNotFoundError as exc:
            logger.error("cannot launch audio child (missing executable): %s", exc)
            return

        assert self._proc is not None
        proc = self._proc
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    line = line.rstrip()
                    if not line:
                        continue
                    logger.info("[audio] %s", line)
                    if self._stop.is_set():
                        break
            proc.wait()
        finally:
            self._proc = None

    def _terminate_child(self, *, timeout: float) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if sys.platform.startswith("linux"):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
        except Exception:
            logger.debug("audio child SIGTERM failed", exc_info=True)
        try:
            proc.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            logger.warning("audio child did not exit on SIGTERM; killing")
        try:
            if sys.platform.startswith("linux"):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            logger.debug("audio child SIGKILL failed", exc_info=True)
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            logger.error("audio child still running after SIGKILL")


def maybe_create_from_env() -> Optional[AudioSupervisor]:
    """Return a configured supervisor when enabled and runnable, else None."""
    if not _truthy(os.environ.get("MEETINGBOX_SPAWN_AUDIO")):
        return None
    script = _resolve_audio_script()
    if script is None:
        logger.error(
            "MEETINGBOX_SPAWN_AUDIO=1 but audio_capture.py not found "
            "(set MEETINGBOX_AUDIO_SCRIPT or run from the mini-pc tree)",
        )
        return None
    return AudioSupervisor(audio_script=script, python=_resolve_python())
