"""Unit tests for the unified approval gate (services.approval).

Covers the real phrases observed in the field session that the old keyword
whitelist wrongly rejected, plus the negations/deferrals that must never pass.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.approval import (  # noqa: E402
    is_affirmation,
    is_negation,
    require_user_approval,
)


APPROVE_PHRASES = [
    # Real utterances from the debugged session that used to be rejected.
    "okay send the email too",
    "send the email",
    "ok send the email",
    "just send it, na",
    "Yes, send it.",
    # Other natural confirmations.
    "yes go ahead",
    "yes",
    "ya send",
    "yeah do it",
    "sure, go ahead",
    "okay go",
    "sounds good",
    "create it",
    "save it",
    "looks good, send",
    # Device button markers.
    "[BUTTON:Confirm] — create the calendar event now with the details on screen.",
]

REJECT_PHRASES = [
    "don't send the invite",
    "do not send it",
    "send it later",
    "maybe later",
    "not now",
    "not yet",
    "no need",
    "cancel that",
    "hold on",
    "wait",
    "no",
    "add Vivek to the invite",
    "please add Vivek",
    "okay add Vivek",
    "change it to 4 PM",
    "remove Shiva",
    "",
    "   ",
]


def test_affirmations_accept_natural_confirmations():
    for p in APPROVE_PHRASES:
        assert is_affirmation(p) is True, f"should accept: {p!r}"


def test_negations_and_deferrals_rejected():
    for p in REJECT_PHRASES:
        assert is_affirmation(p) is False, f"should reject: {p!r}"


def test_is_negation_flags_refusals():
    assert is_negation("don't send it") is True
    assert is_negation("send it later") is True
    assert is_negation("yes go ahead") is False


def test_word_boundaries_avoid_substring_false_matches():
    # "now" must not match the negation word "no".
    assert is_negation("do it now") is False
    assert is_affirmation("go ahead now") is True


def test_edit_requests_are_not_commit_approval():
    for phrase in (
        "add Vivek to the invite",
        "please add Vivek",
        "okay add Vivek",
        "change the time to 4",
        "remove Shiva from the email",
    ):
        assert is_affirmation(phrase) is False


def test_require_user_approval_requires_flag():
    ok, err = require_user_approval(False, "yes send it")
    assert ok is False
    assert err["error"] == "confirmation_required"
    assert err["truth_status"]["writes_committed"] is False


def test_require_user_approval_requires_genuine_phrase():
    ok, err = require_user_approval(True, "don't send it")
    assert ok is False
    assert err["error"] == "confirmation_phrase_required"


def test_require_user_approval_accepts_natural_confirmation():
    ok, err = require_user_approval(True, "okay send the email too")
    assert ok is True
    assert err is None
