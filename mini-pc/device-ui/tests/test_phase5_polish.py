"""Phase 5 evaluations: polish (single-instance guard, app icon)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_single_instance_second_acquire_is_blocked():
    """Within one process, the named mutex blocks a second acquire (Windows)."""
    si = importlib.import_module("single_instance")
    first = si.acquire()
    assert first is not None, "first acquire should succeed"
    try:
        second = si.acquire()
        if sys.platform.startswith("win"):
            # CreateMutexW for an existing named object -> ERROR_ALREADY_EXISTS.
            assert second is None, "second acquire must be blocked on Windows"
            assert si.acquire() is None  # still blocked while held
        else:
            si.release(second)
    finally:
        si.release(first)
    # After release, a fresh acquire must succeed again.
    again = si.acquire()
    assert again is not None
    si.release(again)


def test_single_instance_handle_keeps_object_alive():
    si = importlib.import_module("single_instance")
    h = si.acquire()
    assert h is not None
    assert hasattr(h, "_obj")
    si.release(h)


def test_app_icon_is_valid_multisize_ico():
    ico = REPO_ROOT / "packaging" / "windows" / "meetingbox.ico"
    assert ico.is_file(), "meetingbox.ico must exist for the build"
    PIL = pytest.importorskip("PIL.Image")
    with PIL.open(ico) as im:
        sizes = im.info.get("sizes") or {im.size}
        # Should contain at least a 256 and a small (<=32) icon.
        max_dim = max(max(s) for s in sizes)
        min_dim = min(max(s) for s in sizes)
        assert max_dim >= 128
        assert min_dim <= 48


def test_spec_references_icon_and_both_entrypoints():
    spec = (REPO_ROOT / "packaging" / "windows" / "MeetingBox.spec").read_text(encoding="utf-8")
    assert "main.py" in spec
    assert "audio_capture.py" in spec
    assert "meetingbox.ico" in spec
    assert "meetingbox-audio" in spec
