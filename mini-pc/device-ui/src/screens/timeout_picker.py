"""
Screen Timeout Picker – PRD §5.14

Options: Never, 5 minutes, 10 minutes
"""

from screens.picker_base import PickerBaseScreen


class TimeoutPickerScreen(PickerBaseScreen):
    _title = 'Screen Timeout'
    _description = 'Screen will turn off after inactivity.'
    _options = [
        ('never', 'Never'),
        ('5', 'After 5 minutes'),
        ('10', 'After 10 minutes'),
    ]
    _setting_key = 'screen_timeout'
    _default = 'never'

    def _save_setting(self):
        super()._save_setting()
        app = self.app
        if hasattr(app, '_apply_screen_timeout'):
            app._apply_screen_timeout(self._selected)
