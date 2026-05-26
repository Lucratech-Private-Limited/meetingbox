"""Firmware update channel preference (informational for future auto-update)."""

from screens.picker_base import PickerBaseScreen


class UpdateChannelPickerScreen(PickerBaseScreen):
    _title = "Update channel"
    _description = "Used when automatic update checks are enabled."
    _options = [
        ("stable", "Stable (recommended)"),
        ("beta", "Beta / preview"),
    ]
    _setting_key = "update_channel"
    _default = "stable"

