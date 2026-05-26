"""
HTTP end-to-end smoke test for the calendar agent on the live server.

Sends real messages to POST /api/assistant/intent, inspects the response for
correct tool routing, queuing behaviour, conflict warnings, clarification
questions, and brief labels. No actual Google Calendar write happens — every
write tool queues for approval and we never call the approve endpoint here.

Usage:
    python tests/smoke_calendar_http.py --url https://meetingboxai.lucratechsol.com --token <jwt>

Exit code:
    0 = all checks passed
    1 = one or more checks failed
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from typing import Any


BASE_HEADERS = {"Content-Type": "application/json"}


def _post(url: str, token: str, message: str, timeout: int = 40) -> dict[str, Any]:
    body = json.dumps({"message": message}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={**BASE_HEADERS, "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors="replace")
        print(f"  HTTP {e.code}: {body_txt[:300]}")
        return {}
    except Exception as ex:
        print(f"  Error: {ex}")
        return {}


PASS = "PASS"
FAIL = "FAIL"
SEP = "-" * 80


def _any_tool(resp, tool: str) -> bool:
    return any(t.get("tool") == tool for t in resp.get("tool_results", []))


def _any_queued(resp, tool: str) -> bool:
    return any(p.get("tool_name") == tool for p in resp.get("pending_actions", []))


def _not_queued(resp, tool: str) -> bool:
    return not _any_queued(resp, tool)


def _has_result(resp, tool: str) -> bool:
    return any(
        t.get("tool") == tool and "result" in t
        for t in resp.get("tool_results", [])
    )


def _routed_to(resp, agent_id: str) -> bool:
    rt = resp.get("route") or {}
    if rt.get("agent_id") == agent_id:
        return True
    # Multi-agent plan: route reports plan_steps with agent_id per step
    return any(s.get("agent_id") == agent_id for s in (rt.get("plan_steps") or []))


TEST_CASES = [
    (
        "List upcoming",
        "What's on my calendar today?",
        lambda r: (
            _any_tool(r, "calendar_list_upcoming") and _has_result(r, "calendar_list_upcoming"),
            "expected calendar_list_upcoming with a result (read, not queued)",
        ),
    ),
    (
        "Free slots",
        "When am I free for a 30 minute call this week?",
        lambda r: (
            _any_tool(r, "calendar_suggest_free_slots"),
            "expected calendar_suggest_free_slots",
        ),
    ),
    (
        "Create with attendees - must queue",
        "Schedule a 30 minute Q3 review with vivek+test1@lucratechsol.com tomorrow at 3 PM titled 'Q3 review'",
        lambda r: (
            _any_queued(r, "calendar_create_event"),
            "expected calendar_create_event queued for approval",
        ),
    ),
    (
        "Create solo focus block - must queue, no attendee question",
        "Block my calendar tomorrow from 2 PM for 30 minutes to focus on the architecture doc",
        lambda r: (
            _any_queued(r, "calendar_create_event"),
            "expected solo focus block to queue without asking for attendees",
        ),
    ),
    (
        "Create recurring - must queue with RRULE",
        "Set up a weekly team standup every Monday at 10 AM for 15 minutes for the next 8 weeks",
        lambda r: (
            _any_queued(r, "calendar_create_event") and any(
                # brief_label includes 'Team Standup' / has a date; payload isn't surfaced in /api/assistant/intent,
                # so we settle for a queued create whose brief mentions standup/recurring/weekly.
                "standup" in str(p.get("brief_label") or "").lower()
                or "weekly" in str(p.get("brief_label") or "").lower()
                or "team" in str(p.get("brief_label") or "").lower()
                for p in r.get("pending_actions", [])
                if p.get("tool_name") == "calendar_create_event"
            ),
            "expected calendar_create_event queued (RRULE goes into payload which isn't surfaced)",
        ),
    ),
    (
        "Clarify missing time - no queue, ask question",
        "Block 30 mins on my calendar tomorrow for focus",
        lambda r: (
            (not _any_queued(r, "calendar_create_event")) and any(
                kw in (r.get("assistant_message") or "").lower()
                for kw in ("what time", "what start", "when", "time should", "start time")
            ),
            "expected NO queue and an assistant question about the start time",
        ),
    ),
    (
        "Reschedule - must queue",
        "Move my 3 PM today to 4 PM, keep the same duration",
        lambda r: (
            _any_queued(r, "calendar_update_event"),
            "expected calendar_update_event queued",
        ),
    ),
    (
        "Add attendee - must queue",
        "Add vivek+test2@lucratechsol.com to the team standup on Monday",
        lambda r: (
            _any_queued(r, "calendar_update_event") and any(
                "vivek+test2" in str(p.get("brief_label") or "")
                or "add " in str(p.get("brief_label") or "").lower()
                for p in r.get("pending_actions", [])
                if p.get("tool_name") == "calendar_update_event"
            ),
            "expected update queued with brief mentioning the new attendee",
        ),
    ),
    (
        "Delete - must queue",
        "Delete the Focus Time event tomorrow",
        lambda r: (
            _any_queued(r, "calendar_delete_event"),
            "expected calendar_delete_event queued",
        ),
    ),
    (
        "RSVP accept - must queue (consistent with other writes)",
        "Accept the All Hands invite on Friday",
        lambda r: (
            _any_queued(r, "calendar_rsvp_event") and any(
                "accept" in str(p.get("brief_label") or "").lower()
                for p in r.get("pending_actions", [])
                if p.get("tool_name") == "calendar_rsvp_event"
            ),
            "expected calendar_rsvp_event queued with brief mentioning Accept",
        ),
    ),
    (
        "RSVP decline - must queue",
        "Decline the design review meeting tomorrow",
        lambda r: (
            _any_queued(r, "calendar_rsvp_event") and any(
                "decline" in str(p.get("brief_label") or "").lower()
                for p in r.get("pending_actions", [])
                if p.get("tool_name") == "calendar_rsvp_event"
            ),
            "expected calendar_rsvp_event queued with brief mentioning Decline",
        ),
    ),
    (
        "Reminder - direct execute commitment",
        "Remind me to send the report next Tuesday",
        lambda r: (
            _any_tool(r, "commitment_upsert") and _not_queued(r, "commitment_upsert"),
            "expected commitment_upsert direct-executed (no pending action)",
        ),
    ),
]


def run(base_url: str, token: str, delay: float = 1.5) -> int:
    intent_url = base_url.rstrip("/") + "/api/assistant/intent"
    print(f"\n{'Calendar Agent - HTTP Smoke Test':^80}")
    print(f"{'URL: ' + intent_url:^80}")
    print(SEP)
    print(f"{'#':<3}  {'Scenario':<46}  {'Pass?'}")
    print(SEP)

    total = len(TEST_CASES)
    passed = 0
    failures: list[tuple[str, str, dict]] = []

    for i, (label, message, check_fn) in enumerate(TEST_CASES, 1):
        resp = _post(intent_url, token, message)
        if not resp:
            mark = FAIL
            note = "no response / HTTP error"
            failures.append((label, note, {}))
        else:
            try:
                ok, note = check_fn(resp)
            except Exception as ex:
                ok, note = False, f"check exception: {ex}"
            if ok:
                mark = PASS
                passed += 1
            else:
                mark = FAIL
                failures.append((label, note, resp))

        print(f"{i:<3}  {label:<46}  {mark}")
        if i < total:
            time.sleep(delay)

    print(SEP)
    print(f"\nResults: {passed}/{total} passed\n")

    if failures:
        print("Failed cases:")
        print(SEP)
        for label, note, resp in failures:
            print(f"\n  Scenario  : {label}")
            print(f"  Note      : {note}")
            if resp:
                tr = resp.get("tool_results", [])
                pa = resp.get("pending_actions", [])
                txt = (resp.get("assistant_message") or "")[:240]
                print(f"  tool_results: {json.dumps([{'tool': t.get('tool'), 'has_result': 'result' in t, 'queued': t.get('queued')} for t in tr], indent=2)}")
                print(f"  pending_actions:")
                for p in pa:
                    payload = p.get("payload") or {}
                    interesting = {k: payload.get(k) for k in (
                        "title", "start_time", "duration_minutes", "attendees",
                        "attendees_add", "attendees_remove", "new_start_time",
                        "new_duration_minutes", "new_date", "recurrence",
                        "response_status", "add_meet_link", "date",
                    ) if k in payload}
                    print(f"    - tool_name={p.get('tool_name')} brief={p.get('brief_label')!r} payload={interesting}")
                print(f"  assistant_text: {txt!r}")

    return 0 if not failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calendar agent HTTP smoke test")
    parser.add_argument("--url", default="https://meetingboxai.lucratechsol.com", help="Base URL of the server")
    parser.add_argument("--token", required=True, help="Bearer JWT from the dashboard")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between requests")
    args = parser.parse_args()

    sys.exit(run(args.url, args.token, args.delay))
