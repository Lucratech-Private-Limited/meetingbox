from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_assistant import VoiceCommandInterpreter


def _mk() -> VoiceCommandInterpreter:
    return VoiceCommandInterpreter(
        wake_phrase="hey tony",
        start_commands=["start meeting"],
        command_timeout_seconds=6.0,
        action_cooldown_seconds=2.0,
        confirmation_timeout_seconds=8.0,
    )


def test_combined_wake_and_command_starts_meeting():
    interpreter = _mk()
    intent = interpreter.handle_transcript("hey tony start meeting", now=10.0)
    assert intent is not None
    assert intent.name == "start_meeting"


def test_separate_wake_then_command_starts_meeting():
    interpreter = _mk()
    assert interpreter.handle_transcript("hey tony", now=10.0) is None
    intent = interpreter.handle_transcript("start meeting", now=12.0)
    assert intent is not None
    assert intent.name == "start_meeting"


def test_command_without_wake_is_ignored():
    interpreter = _mk()
    assert interpreter.handle_transcript("start meeting", now=10.0) is None


def test_fuzzy_wake_phrase_allows_minor_transcript_variation():
    interpreter = _mk()
    assert interpreter.handle_transcript("hey toni", now=10.0) is None
    intent = interpreter.handle_transcript("start meeting", now=11.0)
    assert intent is not None
    assert intent.name == "start_meeting"


def test_wake_phrase_times_out():
    interpreter = _mk()
    assert interpreter.handle_transcript("hey tony", now=10.0) is None
    assert interpreter.handle_transcript("start meeting", now=17.0) is None


def test_status_query_after_wake_returns_intent():
    interpreter = _mk()
    assert interpreter.handle_transcript("hey tony", now=10.0) is None
    intent = interpreter.handle_transcript("wifi status", now=11.0)
    assert intent is not None
    assert intent.name == "wifi_status"


def test_brightness_command_captures_value():
    interpreter = _mk()
    intent = interpreter.handle_transcript("hey tony brightness medium", now=10.0)
    assert intent is not None
    assert intent.name == "brightness"
    assert intent.value == "medium"


def test_unsupported_command_returns_fallback_intent():
    interpreter = _mk()
    intent = interpreter.handle_transcript("hey tony speaker test", now=10.0)
    assert intent is not None
    assert intent.name == "unsupported"
    assert intent.value == "speaker_test"


def test_confirmation_accepts_confirm_without_wake():
    interpreter = _mk()
    interpreter.begin_confirmation(now=10.0)
    intent = interpreter.handle_transcript("confirm", now=11.0)
    assert intent is not None
    assert intent.name == "confirm"


def test_confirmation_accepts_cancel_without_wake():
    interpreter = _mk()
    interpreter.begin_confirmation(now=10.0)
    intent = interpreter.handle_transcript("cancel", now=11.0)
    assert intent is not None
    assert intent.name == "cancel"


def test_confirmation_times_out():
    interpreter = _mk()
    interpreter.begin_confirmation(now=10.0)
    assert interpreter.handle_transcript("confirm", now=19.0) is None


def test_help_command_is_supported():
    interpreter = _mk()
    intent = interpreter.handle_transcript("hey tony what can you do", now=10.0)
    assert intent is not None
    assert intent.name == "help"
