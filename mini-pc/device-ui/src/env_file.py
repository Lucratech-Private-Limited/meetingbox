"""Minimal .env-style loader for packaged desktop builds.

The Linux appliance gets its configuration from docker-compose / systemd env.
A standalone Windows install has no such layer, so the installer ships a
``device-ui.env`` file (BACKEND_URL, DASHBOARD_URL, OpenAI/voice toggles, …)
that we load into ``os.environ`` at startup — before ``config`` is imported.

Rules:
* Real environment variables always win (we only ``setdefault``), so a user
  can still override any value from the shell.
* ``KEY=VALUE`` per line; ``#`` comments and blank lines ignored; surrounding
  quotes stripped; no shell expansion.
* Never raises — a missing/garbled file just means "no overrides".
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_FILE_NAME = "device-ui.env"


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    explicit = (os.environ.get("MEETINGBOX_ENV_FILE") or "").strip()
    if explicit:
        paths.append(Path(explicit))

    # Next to the executable (frozen) or the app/src tree (source).
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        paths.append(exe_dir / _ENV_FILE_NAME)
        paths.append(exe_dir.parent / _ENV_FILE_NAME)
    else:
        here = Path(__file__).resolve()
        # device-ui/src -> device-ui, and repo root.
        paths.append(here.parent / _ENV_FILE_NAME)
        paths.append(here.parent.parent / _ENV_FILE_NAME)
        paths.append(here.parent.parent.parent / _ENV_FILE_NAME)

    # Machine-wide config dir on Windows.
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        paths.append(Path(program_data) / "MeetingBox" / _ENV_FILE_NAME)

    # Last-resort: the copy bundled inside the PyInstaller payload (sys._MEIPASS).
    # Used for portable/unsinstalled runs where %PROGRAMDATA% was never seeded.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / _ENV_FILE_NAME)

    return paths


def _parse_line(line: str) -> tuple[str, str] | None:
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if s.lower().startswith("export "):
        s = s[7:].lstrip()
    if "=" not in s:
        return None
    key, value = s.split("=", 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    if not key:
        return None
    return key, value


def load_env_file() -> str | None:
    """Load the first existing ``device-ui.env``. Returns the path loaded, or None."""
    for path in _candidate_paths():
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        applied = 0
        for raw in text.splitlines():
            parsed = _parse_line(raw)
            if parsed is None:
                continue
            key, value = parsed
            if (os.environ.get(key) or "") == "":
                os.environ[key] = value
                applied += 1
        logger.info("Loaded %d env defaults from %s", applied, path)
        return str(path)
    return None
