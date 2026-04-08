"""
Auto-Delete Picker Screen – PRD §5.12

Options: Never, 30 days, 60 days, 90 days
"""

from screens.picker_base import PickerBaseScreen


class AutoDeletePickerScreen(PickerBaseScreen):
    _title = 'Auto-delete Old Meetings'
    _description = ('Meetings older than the selected time will be\n'
                    'automatically deleted to free up storage.')
    _options = [
        ('never', 'Never'),
        ('30', 'After 30 days'),
        ('60', 'After 60 days'),
        ('90', 'After 90 days'),
    ]
    _setting_key = 'auto_delete_days'
    _default = 'never'
