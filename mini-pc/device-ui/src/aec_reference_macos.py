"""macOS far-end reference — thin re-export.

The render-feed reference (app playback PCM as the AEC far end) turned out to
be the right architecture on every desktop OS, not just macOS, so the class
now lives in ``aec_reference.py`` as :class:`AppPlaybackReference`. This
module remains for import compatibility.
"""

from __future__ import annotations

import sys

from aec_reference import AppPlaybackReference  # noqa: F401 - re-export

_IS_MAC = sys.platform == "darwin"


def is_available() -> bool:
    return _IS_MAC
