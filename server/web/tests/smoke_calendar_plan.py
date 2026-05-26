"""
Calendar agent plan-level smoke test.

Calls plan_calendar_steps() with a set of real-world messages and prints which
tool(s) the LLM picks for each. No Google Calendar API is called.

Special-case: missing-required-field scenarios should return a 'clarify' sentinel
step asking ONE focused question instead of queuing a half-complete create.

Usage (from server/web/):
    python tests/smoke_calendar_plan.py

Requirements:
    ANTHROPIC_API_KEY env var must be set (it calls Claude to plan).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant_service import plan_calendar_steps, CALENDAR_TOOLS


# Each tuple is (description, message, expected_tool[s])
# Use 'clarify' as the expected tool for missing-required-field scenarios.
TEST_CASES: list[tuple[str, str, str]] = [
    # Reads
    ("List today",            "What's on my calendar today?",                                       "calendar_list_upcoming"),
    ("List upcoming",         "Show my schedule for this week",                                     "calendar_list_upcoming"),
    ("Free slots",            "When am I free for a 30 minute call this week?",                     "calendar_suggest_free_slots"),
    ("Free slots specific",   "Find me an open 45 minute window between Mon and Fri",               "calendar_suggest_free_slots"),
    # Creates — fully specified, should queue create
    ("Create with attendees",
     "Schedule a 30 minute sync with john@example.com tomorrow at 3 PM titled 'Q3 review'",
     "calendar_create_event"),
    ("Create solo block",
     "Block my calendar tomorrow from 2 PM to 3 PM for focus work on the architecture doc",
     "calendar_create_event"),
    ("Create recurring",
     "Set up a weekly team standup every Monday at 10 AM for 15 minutes for the next 8 weeks",
     "calendar_create_event"),
    ("Create with no link",
     "Schedule a 1:1 with sarah@company.com Friday at 11 AM, no meet link, 30 minutes, title 'Sarah catchup'",
     "calendar_create_event"),
    # Missing-field — should return 'clarify'
    ("Clarify missing time",  "Block 30 mins on my calendar tomorrow for focus",                    "clarify"),
    ("Clarify missing duration",
     "Schedule a meeting with john@example.com tomorrow at 3 PM titled 'sync'",
     "clarify"),
    ("Clarify missing title", "Put something on my calendar tomorrow at 4 PM for 30 minutes",       "clarify"),
    # Updates (reschedule + attendee changes)
    ("Reschedule time",
     "Move my 3 PM today to 4 PM, keep the duration",
     "calendar_update_event"),
    ("Change date",
     "Push the Catch Up meeting from tomorrow to Thursday",
     "calendar_update_event"),
    ("Add attendee",
     "Add david@example.com to the team standup on Monday",
     "calendar_update_event"),
    ("Remove attendee",
     "Drop alex@example.com from the Friday review meeting",
     "calendar_update_event"),
    ("Rename event",
     "Rename my 'Catch Up' meeting tomorrow to 'Quarterly Sync'",
     "calendar_update_event"),
    # Delete
    ("Delete by name+date",   "Delete the Focus Time event tomorrow",                               "calendar_delete_event"),
    ("Cancel meeting",        "Cancel my 4 PM with marketing on Friday",                            "calendar_delete_event"),
    # RSVP
    ("RSVP accept",           "Accept the All Hands invite on Friday",                              "calendar_rsvp_event"),
    ("RSVP decline",          "Decline the design review meeting tomorrow",                         "calendar_rsvp_event"),
    ("RSVP tentative",        "Mark myself as tentative for the marketing sync on Thursday",         "calendar_rsvp_event"),
    # Commitments (still owned by calendar agent)
    ("Reminder",              "Remind me to send the report next Tuesday",                          "commitment_upsert"),
    ("List todos",            "Show me my reminders",                                               "commitment_list"),
]

PASS_MARK = "PASS"
FAIL_MARK = "FAIL"
WARN_MARK = "WARN"
SEPARATOR = "-" * 90


def _tool_of_step(s: dict[str, Any]) -> str:
    return str(s.get("tool") or "")


def run() -> int:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Set it and re-run.")
        return 1

    total = len(TEST_CASES)
    passed = 0
    wrong_tool = 0
    no_plan = 0

    print(f"\n{'Calendar Agent - Plan Smoke Test':^90}")
    print(SEPARATOR)
    print(f"{'#':<3}  {'Scenario':<26}  {'Expected':<28}  {'Got':<28}  {'OK?'}")
    print(SEPARATOR)

    for i, (label, message, hint) in enumerate(TEST_CASES, 1):
        steps: list[dict[str, Any]] = plan_calendar_steps(message)
        valid = {*CALENDAR_TOOLS, "commitment_upsert", "commitment_list", "clarify"}
        tools_got = [_tool_of_step(s) for s in steps if _tool_of_step(s) in valid]

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

        print(f"{i:<3}  {label:<26}  {hint:<28}  {got_display:<28}  {mark}")

    print(SEPARATOR)
    print(f"\nResults: {passed}/{total} exact match  |  {wrong_tool} different tool  |  {no_plan} no plan\n")

    if (wrong_tool + no_plan) > 0:
        print("Details for non-passing cases:")
        print(SEPARATOR)
        for i, (label, message, hint) in enumerate(TEST_CASES, 1):
            steps = plan_calendar_steps(message)
            valid = {*CALENDAR_TOOLS, "commitment_upsert", "commitment_list", "clarify"}
            tools_got = [_tool_of_step(s) for s in steps if _tool_of_step(s) in valid]
            if not tools_got or hint not in tools_got:
                print(f"\n[{i}] {label}")
                print(f"  Message  : {message}")
                print(f"  Expected : {hint}")
                if steps:
                    for s in steps:
                        print(f"    tool={s.get('tool')}  args={s.get('args')}")
                else:
                    print("    (empty plan)")

    return 0 if no_plan == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
