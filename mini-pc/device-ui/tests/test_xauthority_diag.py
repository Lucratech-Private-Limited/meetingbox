"""Tests for Xauthority :0 detection (Docker + local X11 diagnostics)."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xauthority_util import display_refers_to_screen_zero, xauthority_list_has_display_zero


def test_display_refers_to_screen_zero():
    assert display_refers_to_screen_zero(":0")
    assert display_refers_to_screen_zero(":0.0")
    assert not display_refers_to_screen_zero(":10")
    assert not display_refers_to_screen_zero(":10.0")
    assert not display_refers_to_screen_zero("")


def test_xauthority_has_display_zero_common_formats():
    assert xauthority_list_has_display_zero(
        ":0  MIT-MAGIC-COOKIE-1  deadbeef\n"
    )
    assert xauthority_list_has_display_zero(
        ":0.0  MIT-MAGIC-COOKIE-1  deadbeef\n"
    )
    assert xauthority_list_has_display_zero(
        "meetingbox/unix:0  MIT-MAGIC-COOKIE-1  deadbeef\n"
    )
    assert xauthority_list_has_display_zero(
        "localhost:0.0  MIT-MAGIC-COOKIE-1  deadbeef\n"
    )


def test_xauthority_excludes_ssh_forwarding():
    assert not xauthority_list_has_display_zero(
        "localhost:10.0  MIT-MAGIC-COOKIE-1  deadbeef\n"
    )
