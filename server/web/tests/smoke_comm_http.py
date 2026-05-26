"""
HTTP end-to-end smoke test for the communication agent on the live server.

Sends real messages to POST /api/assistant/intent, inspects the response
for correct tool routing, queuing behaviour, and brief labels.
No actual email is sent — outbound tools queue for approval and we never
call the approve endpoint here.

Usage:
    python tests/smoke_comm_http.py --url https://meetingboxai.lucratechsol.com --token <your-jwt>

The JWT is the dashboard bearer token from your browser session.
To get it:
  1. Open MeetingBox dashboard in Chrome.
  2. DevTools -> Application -> Local Storage -> find 'token' or 'access_token'.
     OR Network tab -> any /api/ request -> Headers -> Authorization: Bearer <token>.

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


def _post(url: str, token: str, message: str, timeout: int = 30) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Test cases: (description, message, check_fn(response) -> (ok, note))
# ---------------------------------------------------------------------------

def _any_tool(resp, tool: str) -> bool:
    return any(t.get("tool") == tool for t in resp.get("tool_results", []))

def _any_queued(resp, tool: str) -> bool:
    return any(
        p.get("tool_name") == tool
        for p in resp.get("pending_actions", [])
    )

def _not_queued(resp, tool: str) -> bool:
    return not _any_queued(resp, tool)

def _has_result(resp, tool: str) -> bool:
    return any(
        t.get("tool") == tool and "result" in t
        for t in resp.get("tool_results", [])
    )


TEST_CASES = [
    (
        "List inbox",
        "Check my recent emails",
        lambda r: (
            _any_tool(r, "gmail_list_recent") and _has_result(r, "gmail_list_recent"),
            "expected gmail_list_recent with a result (not queued)"
        ),
    ),
    (
        "Search by sender",
        "Any unread emails from last week?",
        lambda r: (
            _any_tool(r, "gmail_list_recent"),
            "expected gmail_list_recent"
        ),
    ),
    (
        "Create draft — direct execute",
        "Draft an email to john@example.com with subject 'Q3 review meeting' and say 'Hi John, can we schedule a call?'",
        lambda r: (
            _any_tool(r, "gmail_create_draft") and _has_result(r, "gmail_create_draft") and _not_queued(r, "gmail_create_draft"),
            "expected gmail_create_draft as direct-execute (no pending action)"
        ),
    ),
    (
        "Send email — must queue",
        "Send an email to vivek@lucratechsol.com with subject 'smoke test' saying 'this is a smoke test, please ignore'",
        lambda r: (
            _any_tool(r, "gmail_send_email") and _any_queued(r, "gmail_send_email"),
            "expected gmail_send_email to be queued for approval, NOT sent"
        ),
    ),
    (
        "Send email — Gmail API not called (check pending, not sent)",
        "Send a message to nobody@example.com subject 'API test' body 'Testing the queue'",
        lambda r: (
            # If it appears in pending_actions it was NOT sent; that is the correct behaviour.
            _any_queued(r, "gmail_send_email") or
            # Alternatively the LLM may route to create_draft — also acceptable
            _any_tool(r, "gmail_create_draft"),
            "expected either gmail_send_email queued or gmail_create_draft for ambiguous send"
        ),
    ),
    (
        "Reply all — must queue + text mentions reply-all",
        "Reply all on thread abc123 saying 'Thanks everyone, I will follow up'",
        lambda r: (
            _any_queued(r, "gmail_reply_all") and (
                "reply-all" in (r.get("assistant_message") or "").lower()
                or "reply all" in (r.get("assistant_message") or "").lower()
            ),
            "expected gmail_reply_all queued AND assistant_message to mention reply-all"
        ),
    ),
    (
        "Archive — must queue",
        "Archive the latest email from sarah",
        lambda r: (
            _any_queued(r, "gmail_archive_email") or _any_tool(r, "gmail_list_recent"),
            "expected either archive queued or list (to find the message) — both acceptable"
        ),
    ),
    (
        "Delete — must queue, not permanent",
        "Delete email with id MSG_SMOKE_TEST",
        lambda r: (
            _any_queued(r, "gmail_delete_email"),
            "expected gmail_delete_email queued (trash, not permanent delete)"
        ),
    ),
]


def run(base_url: str, token: str, delay: float = 1.5) -> int:
    intent_url = base_url.rstrip("/") + "/api/assistant/intent"
    print(f"\n{'Communication Agent — HTTP Smoke Test':^80}")
    print(f"{'URL: ' + intent_url:^80}")
    print(SEP)
    print(f"{'#':<3}  {'Scenario':<40}  {'Pass?'}")
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
            ok, note = check_fn(resp)
            if ok:
                mark = PASS
                passed += 1
            else:
                mark = FAIL
                failures.append((label, note, resp))

        print(f"{i:<3}  {label:<40}  {mark}")
        if i < total:
            time.sleep(delay)  # avoid rate-limit

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
                txt = (resp.get("assistant_message") or "")[:200]
                print(f"  tool_results: {json.dumps([{'tool': t.get('tool'), 'has_result': 'result' in t, 'queued': t.get('queued')} for t in tr], indent=2)}")
                print(f"  pending_actions: {json.dumps([p.get('tool_name') for p in pa])}")
                print(f"  assistant_text: {txt!r}")

    return 0 if not failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Communication agent HTTP smoke test")
    parser.add_argument("--url", default="https://meetingboxai.lucratechsol.com", help="Base URL of the server")
    parser.add_argument("--token", required=True, help="Bearer JWT from the dashboard")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between requests (default 1.5)")
    args = parser.parse_args()

    sys.exit(run(args.url, args.token, args.delay))
