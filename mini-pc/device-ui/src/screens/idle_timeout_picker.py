"""
Idle Screen Timeout Picker

Replaces the older Screen Timeout (display-off) picker. The idle screen is
the only "screensaver" state — there is no longer a backlight-off path —
so the configurable value here controls how long the device stays on the
home / meetings / settings screen before showing the lock screen.

Values are stored as **seconds** (or ``"never"``). The legacy backend key
``screen_timeout`` (which stored minutes) is unused; we read/write a new
key, ``idle_screen_timeout``.
"""

from screens.picker_base import PickerBaseScreen


class IdleTimeoutPickerScreen(PickerBaseScreen):
    _title = "Idle Screen"
    _description = "After this much inactivity the lock-screen idle UI appears. Tap anywhere to return to home."
    _options = [
        ("30", "After 30 seconds"),
        ("60", "After 1 minute"),
        ("120", "After 2 minutes"),
        ("300", "After 5 minutes"),
        ("never", "Never"),
    ]
    _setting_key = "idle_screen_timeout"
    _default = "30"

    def _save_setting(self):
        super()._save_setting()
        app = self.app
        if hasattr(app, "_apply_idle_timeout"):
            app._apply_idle_timeout(self._selected)
