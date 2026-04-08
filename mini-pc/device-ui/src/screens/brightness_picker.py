"""
Screen Brightness Picker – PRD §5.13

Options: Low, Medium, High
"""

from screens.picker_base import PickerBaseScreen
from hardware import set_brightness


class BrightnessPickerScreen(PickerBaseScreen):
    _title = 'Screen Brightness'
    _description = 'Linux sysfs backlight on the appliance; may have no effect on a dev PC (setting is still saved).'
    _options = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]
    _setting_key = 'brightness'
    _default = 'high'

    def _save_setting(self):
        super()._save_setting()
        set_brightness(self._selected)
