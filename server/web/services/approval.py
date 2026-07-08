"""Single source of truth for "did the user actually approve this write?".

Every committing voice tool (send email, create/update calendar event, create
task) routes its approval decision through :func:`require_user_approval` so the
behaviour is identical everywhere instead of each tool inventing its own gate.

The judgement is deterministic and intent-based (no LLM, no latency): it keys on
affirmation cues and the ABSENCE of negation/deferral, so it generalises to
phrasings we have never seen ("ya send", "okay go", "sounds good") instead of an
exact-phrase whitelist. Device button taps arrive as machine markers and are
trusted verbatim.
"""

from __future__ import annotations

import re
from typing import Any

# Device-originated approval markers. The device injects these as the user turn
# when a physical Confirm/Send button is tapped (see device-ui main.py), so they
# are an unambiguous, trusted approval.
_TRUSTED_MARKERS = ("[button:confirm]", "[button:send]", "[button:approve]")

# Negation / deferral cues. If ANY of these is present the utterance is NOT an
# approval, even when an affirmation word also appears ("yes, but not now").
_NEGATION_WORDS = (
    "don't", "dont", "no", "not", "never", "cancel", "stop", "wait",
    "nope", "nah", "later", "hold",
)
_NEGATION_PHRASES = (
    "do not", "not now", "not yet", "no need", "hold on", "hold off",
    "send it later", "maybe later", "don't send", "do not send",
)

# Affirmation cues. Single words are matched on word boundaries so short tokens
# ("no", "ok", "k", "ya", "go") never match inside larger words.
_AFFIRM_WORDS = (
    "yes", "yeah", "yep", "yup", "ya", "sure", "ok", "okay", "k", "confirm",
    "confirmed", "approve", "approved", "send", "proceed",
    "absolutely", "definitely", "correct", "perfect", "fine",
)
_AFFIRM_PHRASES = (
    "go ahead", "go for it", "do it", "send it", "sounds good", "looks good",
    "make it so", "fire away", "ship it", "save it", "create it", "yes please",
    "that's right", "thats right", "all good",
)

# These are edit/refinement intents, not approval intents. They commonly occur
# while a draft card is still being reviewed ("add Vivek", "change it to 4"),
# and must never be allowed to masquerade as permission to commit the card.
_EDIT_WORDS = (
    "add", "remove", "change", "edit", "update", "replace", "invite",
    "include", "cc", "bcc", "move", "reschedule", "rename",
)
_COMMIT_WORDS = (
    "confirm", "confirmed", "approve", "approved", "send", "save", "create",
    "proceed",
)
_COMMIT_PHRASES = (
    "go ahead", "go for it", "do it", "send it", "save it", "create it",
)


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    for w in words:
        # Escape so tokens like "don't" are treated literally; \b anchors avoid
        # substring hits ("no" in "now", "go" in "good").
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text):
            return True
    return False


def is_negation(text: str) -> bool:
    """True if the utterance expresses a refusal or deferral."""
    t = _norm(text)
    if not t:
        return False
    if any(p in t for p in _NEGATION_PHRASES):
        return True
    return _has_word(t, _NEGATION_WORDS)


def is_affirmation(text: str) -> bool:
    """True if the utterance expresses genuine approval intent.

    Trusted device button markers count as approval. Otherwise the text must
    contain an affirmation cue AND contain no negation/deferral cue.
    """
    t = _norm(text)
    if not t:
        return False
    if any(m in t for m in _TRUSTED_MARKERS):
        return True
    if is_negation(t):
        return False
    has_edit_intent = _has_word(t, _EDIT_WORDS)
    has_commit_intent = _has_word(t, _COMMIT_WORDS) or any(p in t for p in _COMMIT_PHRASES)
    if has_edit_intent and not has_commit_intent:
        return False
    if any(p in t for p in _AFFIRM_PHRASES):
        return True
    return _has_word(t, _AFFIRM_WORDS)


def _confirmation_error(error: str, detail: str) -> dict[str, Any]:
    return {
        "error": error,
        "detail": detail,
        "truth_status": {
            "writes_committed": False,
            "note": "No write executed because user approval was not established.",
        },
    }


def require_user_approval(
    confirmed_by_user: Any,
    phrase: str | None,
) -> tuple[bool, dict[str, Any] | None]:
    """Uniform approval gate for every committing tool.

    Returns ``(True, None)`` when the user has genuinely approved, otherwise
    ``(False, error_dict)`` where ``error_dict`` is a structured payload the
    tool should return verbatim so the model re-asks instead of writing.

    ``confirmed_by_user`` is the model-asserted structured flag (primary gate);
    ``phrase`` is the user's actual approving words or a device button marker,
    validated by intent so natural confirmations all pass and refusals/deferrals
    are rejected.
    """
    if confirmed_by_user is not True:
        return False, _confirmation_error(
            "confirmation_required",
            "Explicit user confirmation is required before executing this action.",
        )
    if not is_affirmation(phrase or ""):
        return False, _confirmation_error(
            "confirmation_phrase_required",
            "Provide the user's explicit confirmation (their approving words or a Confirm/Send tap).",
        )
    return True, None
