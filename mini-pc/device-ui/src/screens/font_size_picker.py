"""UI font sizing — scales other screens via ``config.set_ui_font_preset``."""

from config import set_ui_font_preset
from screens.picker_base import PickerBaseScreen


class FontSizePickerScreen(PickerBaseScreen):
    _title = "Font size"
    _description = "Affects list and form text across most secondary screens."
    _options = [
        ("small", "Small"),
        ("medium", "Medium"),
        ("large", "Large"),
    ]
    _setting_key = "font_size"
    _default = "medium"

    def _save_setting(self):
        set_ui_font_preset(self._selected, persist=True)
        super()._save_setting()

