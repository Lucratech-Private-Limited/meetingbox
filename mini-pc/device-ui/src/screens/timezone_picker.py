"""Timezone — saved to backend settings and applied via timedatectl (local) + backend API."""

from async_helper import run_async
from screens.picker_base import PickerBaseScreen
from system_clock_util import try_set_timezone

_TZ_ROWS = [
    ("UTC", "UTC"),
    ("America/New_York", "America — New York"),
    ("America/Chicago", "America — Chicago"),
    ("America/Denver", "America — Denver"),
    ("America/Los_Angeles", "America — Los Angeles"),
    ("America/Toronto", "America — Toronto"),
    ("America/Mexico_City", "America — Mexico City"),
    ("America/Sao_Paulo", "America — São Paulo"),
    ("Europe/London", "Europe — London"),
    ("Europe/Paris", "Europe — Paris"),
    ("Europe/Berlin", "Europe — Berlin"),
    ("Asia/Dubai", "Asia — Dubai"),
    ("Asia/Kolkata", "Asia — India (Kolkata)"),
    ("Asia/Shanghai", "Asia — Shanghai"),
    ("Asia/Tokyo", "Asia — Tokyo"),
    ("Asia/Singapore", "Asia — Singapore"),
    ("Australia/Sydney", "Australia — Sydney"),
    ("Pacific/Auckland", "Pacific — Auckland"),
]


class TimezonePickerScreen(PickerBaseScreen):
    _title = "Timezone"
    _description = "Applies on the MeetingBox host via timedatectl when permitted."
    _options = _TZ_ROWS
    _setting_key = "timezone"
    _default = "UTC"

    def _save_setting(self):
        super()._save_setting()
        tz = self._selected
        try_set_timezone(tz)
        run_async(self.backend.set_timezone(tz))
