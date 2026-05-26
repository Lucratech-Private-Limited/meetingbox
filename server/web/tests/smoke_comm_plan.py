"""
Communication agent plan-level smoke test.

Calls plan_communication_steps() with a set of real-world messages and prints
which tool(s) the LLM picks for each. No Gmail API is called.

Usage (from server/web/):
    python tests/smoke_comm_plan.py

Requirements:
    ANTHROPIC_API_KEY env var must be set (it calls Claude to generate the plan).

Exit code:
    0 = all messages produced at least one valid Gmail tool
    1 = one or more messages produced no plan (LLM failed / key missing)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant_service import plan_communication_steps, GMAIL_TOOLS


# Each tuple is (description, message, expected_tool(s) hint for display)
TEST_CASES: list[tuple[str, str, str]] = [
    # Read / search
    ("List inbox",            "Check my inbox",                                                "gmail_list_recent"),
    ("Search unread",         "Show me unread emails from last week",                          "gmail_list_recent"),
    ("Search by sender",      "Any emails from sarah@company.com?",                            "gmail_list_recent"),
    # Drafts (direct execute)
    ("Create draft",          "Draft an email to john@example.com about the Q3 review",       "gmail_create_draft"),
    ("Create draft no recip", "Draft something I can send to the team later",                  "gmail_create_draft"),
    ("Save draft",            "Save that as a draft for now",                                  "gmail_create_draft"),
    # Outbound (should queue)
    ("Send email",            "Send an email to david@example.com: subject 'Quick update', body 'Just checking in'",  "gmail_send_email"),
    ("Send now",              "Send it now to mike@example.com",                               "gmail_send_email"),
    # Reply / reply-all / forward (should queue)
    ("Reply to thread",       "Reply to thread abc123 saying 'Thanks, I'll review it'",       "gmail_reply_to_thread"),
    ("Reply all",             "Reply all on thread xyz789 saying 'All noted'",                 "gmail_reply_all"),
    ("Forward",               "Forward message id msg123 to fwd@example.com",                  "gmail_forward_email"),
    # Archive / delete (should queue)
    ("Archive",               "Archive the email from last Tuesday",                           "gmail_archive_email"),
    ("Delete",                "Trash the email with id DEL99",                                 "gmail_delete_email"),
    # Ambiguous — should still pick a Gmail tool
    ("Ambiguous: reply",      "Write back to the marketing team thread",                       "gmail_reply_to_thread"),
]

PASS_MARK = "\u2705"
FAIL_MARK = "\u274c"
WARN_MARK = "\u26a0\ufe0f"
SEPARATOR = "\u2500" * 78


def run() -> int:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Set it and re-run.")
        return 1

    total = len(TEST_CASES)
    passed = 0
    wrong_tool = 0
    no_plan = 0

    print(f"\n{'Communication Agent — Plan Smoke Test':^78}")
    print(SEPARATOR)
    print(f"{'#':<3}  {'Scenario':<28}  {'Expected':<26}  {'Got':<26}  {'OK?'}")
    print(SEPARATOR)

    for i, (label, message, hint) in enumerate(TEST_CASES, 1):
        steps: list[dict[str, Any]] = plan_communication_steps(message)
        tools_got = [s.get("tool", "") for s in steps if s.get("tool") in GMAIL_TOOLS]

        if not tools_got:
            mark = FAIL_MARK
            no_plan += 1
            got_display = "(no plan)"
        elif hint in tools_got:
            mark = PASS_MARK
            passed += 1
            got_display = ", ".join(tools_got)
        else:
            mark = WARN_MARK
            wrong_tool += 1
            got_display = ", ".join(tools_got)

        print(f"{i:<3}  {label:<28}  {hint:<26}  {got_display:<26}  {mark}")

    print(SEPARATOR)
    print(f"\nResults: {passed}/{total} exact match  |  {wrong_tool} different tool  |  {no_plan} no plan\n")

    # Print full step details for any case that didn't match
    any_issue = wrong_tool + no_plan
    if any_issue:
        print("Details for non-passing cases:")
        print(SEPARATOR)
        for i, (label, message, hint) in enumerate(TEST_CASES, 1):
            steps = plan_communication_steps(message)
            tools_got = [s.get("tool", "") for s in steps if s.get("tool") in GMAIL_TOOLS]
            if not tools_got or hint not in tools_got:
                print(f"\n[{i}] {label}")
                print(f"  Message  : {message}")
                print(f"  Expected : {hint}")
                print(f"  Got steps:")
                if steps:
                    for s in steps:
                        print(f"    tool={s.get('tool')}  is_write={s.get('is_write')}  args={s.get('args')}")
                else:
                    print("    (empty plan — LLM returned nothing or plan was filtered)")

    return 0 if no_plan == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
