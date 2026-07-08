"""Desktop de-appliance pass: platform flags and copy verb.

These assert the central gating primitives the desktop port relies on to hide
appliance-only UI (Quick Panel, idle lock screen, brightness, OTA, etc.) and to
use mouse wording instead of touch wording.
"""

from __future__ import annotations

import platform_compat


def test_is_desktop_matches_windows_or_macos():
    assert platform_compat.IS_DESKTOP == (
        platform_compat.IS_WINDOWS or platform_compat.IS_MACOS
    )


def test_is_desktop_is_inverse_of_linux():
    assert platform_compat.IS_DESKTOP == (not platform_compat.IS_LINUX)


def test_tap_or_click_word_matches_platform():
    if platform_compat.IS_DESKTOP:
        assert platform_compat.TAP_OR_CLICK == "Click"
        assert platform_compat.tap_or_click == "click"
    else:
        assert platform_compat.TAP_OR_CLICK == "Tap"
        assert platform_compat.tap_or_click == "tap"


def test_settings_gating_flag_tracks_desktop():
    import importlib

    settings = importlib.import_module("screens.settings")
    # Appliance rows are shown only on the Linux appliance.
    assert settings._SHOW_APPLIANCE_ROWS == (not platform_compat.IS_DESKTOP)
    assert settings._DESKTOP_BUILD == platform_compat.IS_DESKTOP
