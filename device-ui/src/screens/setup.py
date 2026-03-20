"""
Setup Screen (Legacy compatibility wrapper)

This screen has been replaced by the boot-flow sequence:
  splash → welcome → wifi_setup → setup_progress → all_set

Kept for backward compatibility. Redirects to the new wifi_setup screen.
"""

from screens.base_screen import BaseScreen


class SetupScreen(BaseScreen):
    """Legacy setup screen – redirects to wifi_setup."""

    def on_enter(self):
        self.goto('wifi_setup', transition='fade')
