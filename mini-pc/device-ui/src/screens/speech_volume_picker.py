"""
Assistant speech volume — espeak-ng amplitude for wake-word TTS replies.
"""

from kivy.clock import Clock

from async_helper import run_async
from screens.picker_base import PickerBaseScreen


class SpeechVolumePickerScreen(PickerBaseScreen):
    _title = "Assistant voice volume"
    _description = "How loud the device speaks after wake word commands (local voice replies)."
    _options = [
        ("25", "25% — quiet"),
        ("50", "50%"),
        ("75", "75%"),
        ("85", "85% — recommended"),
        ("100", "100% — loudest"),
    ]
    _setting_key = "assistant_speech_volume"
    _default = "85"

    def on_enter(self):
        async def _load():
            try:
                settings = await self.backend.get_settings()
                raw = settings.get(self._setting_key, self._default)
                if isinstance(raw, (int, float)):
                    saved = str(int(raw))
                else:
                    saved = str(raw).strip() or self._default
                valid = {v for v, _ in self._options}
                if saved not in valid:
                    saved = self._default

                def _apply(_dt):
                    self._selected = saved
                    for r in self._rows:
                        r.set_selected(r._value == saved)

                Clock.schedule_once(_apply, 0)
            except Exception:
                pass

        run_async(_load())

    def _save_setting(self):
        async def _save():
            try:
                v = int(float(self._selected))
                v = max(0, min(100, v))
                await self.backend.update_settings({self._setting_key: v})

                def _apply(_dt):
                    self.app.assistant_speech_volume = v

                Clock.schedule_once(_apply, 0)
            except Exception:
                pass

        run_async(_save())
