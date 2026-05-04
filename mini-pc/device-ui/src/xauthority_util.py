"""Helpers to interpret ``xauth list`` output for local display :0 (Docker + X11)."""

from __future__ import annotations

import re


def display_refers_to_screen_zero(disp: str) -> bool:
    """True if DISPLAY is local screen 0 (:0 or :0.N), not :10 (SSH) etc."""
    d = (disp or "").strip().lower()
    if not d.startswith(":"):
        return False
    if d.startswith(":0"):
        return len(d) == 2 or (len(d) > 2 and d[2] == ".")
    return False


def xauthority_list_has_display_zero(cookie_text: str) -> bool:
    """True if xauth list output likely includes authority for local display :0 / :0.N."""
    low = cookie_text.lower()
    if "unix:0" in low:
        return True
    for line in cookie_text.splitlines():
        parts = line.split()
        if not parts:
            continue
        fam = parts[0]
        flo = fam.lower()
        if re.search(r":(10|11)(\.|$)", flo):
            continue
        if flo.startswith(":0"):
            if len(flo) == 2:
                return True
            if len(flo) > 2 and flo[2] == "." and flo[3:].isdigit():
                return True
            continue
        if re.match(r"^[^:/]+:0(\.\d+)?$", flo):
            return True
        if re.search(r"/unix:0(\.\d+)?$", flo):
            return True
    return False
