"""Pytest configuration for the MeetingBox device-ui Windows port test suite.

Adds ``device-ui/src`` to ``sys.path`` so tests can import the UI modules the
same way ``main.py`` does (the app runs with ``PYTHONPATH=src``). Also forces
mock/offline-friendly defaults so importing ``config`` never reaches the
network or a real backend.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# audio_capture.py lives in the repo-level ``audio/`` folder (sibling of device-ui).
_AUDIO = Path(__file__).resolve().parents[2] / "audio"
if _AUDIO.is_dir() and str(_AUDIO) not in sys.path:
    sys.path.insert(0, str(_AUDIO))

# Keep config import side effects offline and deterministic.
os.environ.setdefault("MOCK_BACKEND", "1")
os.environ.setdefault("LOG_TO_CONSOLE", "0")
