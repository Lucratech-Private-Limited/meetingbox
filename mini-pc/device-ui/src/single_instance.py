"""Single-instance guard for the desktop build.

The Linux appliance is the only process on a kiosk, but on Windows a user can
double-click the shortcut several times. Running two UIs fights over the audio
device, the config dir, and the backend websocket. We take a named OS mutex;
the second instance detects it and bows out.

Linux/macOS use an exclusive lock on a file in the per-user data dir.
Returns a handle that must be kept alive for the lifetime of the process
(store it on the App), or ``None`` if another instance already holds the lock.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

_MUTEX_NAME = "Global\\MeetingBoxUI_SingleInstance_v1"


class _Handle:
    """Opaque keep-alive holder so the lock isn't GC'd/closed early."""

    def __init__(self, obj) -> None:
        self._obj = obj


def acquire() -> _Handle | None:
    """Return a keep-alive handle, or ``None`` if another instance is running."""
    if sys.platform.startswith("win"):
        return _acquire_windows()
    return _acquire_posix()


def release(handle: "_Handle | None") -> None:
    """Release a previously acquired lock.

    Production never calls this (process exit frees the OS mutex/lock); it
    exists so tests can acquire/release repeatedly within one process.
    """
    if handle is None:
        return
    obj = getattr(handle, "_obj", None)
    if obj is None:
        return
    try:
        if sys.platform.startswith("win"):
            import ctypes

            ctypes.windll.kernel32.ReleaseMutex(obj)
            ctypes.windll.kernel32.CloseHandle(obj)
        else:
            import fcntl  # type: ignore

            fcntl.flock(obj.fileno(), fcntl.LOCK_UN)
            obj.close()
    except Exception:
        pass
    finally:
        handle._obj = None


def _acquire_windows() -> _Handle | None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        handle = kernel32.CreateMutexW(None, wintypes.BOOL(True), _MUTEX_NAME)
        last_error = kernel32.GetLastError()
        if not handle:
            logger.warning("single-instance: CreateMutexW failed (err=%s)", last_error)
            return _Handle(None)  # fail open
        if last_error == ERROR_ALREADY_EXISTS:
            # CreateMutexW still returns a valid handle to the existing object;
            # close it so we don't keep the named mutex alive ourselves.
            kernel32.CloseHandle(handle)
            logger.warning("single-instance: another MeetingBox instance is already running")
            return None
        return _Handle(handle)
    except Exception as e:  # never block startup on the guard itself
        logger.warning("single-instance: guard unavailable (%s); continuing", e)
        return _Handle(None)


def _acquire_posix() -> _Handle | None:
    try:
        import fcntl  # type: ignore

        base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
        lock_path = os.path.join(base, "meetingbox-ui.lock")
        f = open(lock_path, "w")
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.warning("single-instance: lock held by another instance (%s)", lock_path)
            f.close()
            return None
        return _Handle(f)
    except Exception as e:
        logger.warning("single-instance: guard unavailable (%s); continuing", e)
        return _Handle(None)
