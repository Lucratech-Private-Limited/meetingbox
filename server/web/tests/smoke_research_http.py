"""
HTTP end-to-end smoke test for the research agent on the live server.

Sends real messages to POST /api/assistant/intent, inspects the response for
correct tool routing, direct (non-queued) execution, and a sensible
assistant_message. Some checks (weather/news/web_search) hit live APIs through
the server, so the live server must have network egress.

Usage:
    python tests/smoke_research_http.py --url https://meetingboxai.lucratechsol.com --token <jwt>

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


def _post(url: str, token: str, message: str, timeout: int = 90) -> dict[str, Any]:
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
SEP = "-" * 90


def _any_tool(resp, tool: str) -> bool:
    return any(t.get("tool") == tool for t in resp.get("tool_results", []))


def _has_result(resp, tool: str) -> bool:
    return any(
        t.get("tool") == tool and "result" in t
        for t in resp.get("tool_results", [])
    )


def _no_pending(resp) -> bool:
    return not resp.get("pending_actions")


def _msg_contains(resp, *needles: str) -> bool:
    text = (resp.get("assistant_message") or "").lower()
    return all(n.lower() in text for n in needles)


TEST_CASES = [
    (
        "Web search general",
        "Look up the basics of vector databases",
        lambda r: (
            _any_tool(r, "research_web_search") and _no_pending(r),
            "expected research_web_search direct-executed (no pending)",
        ),
    ),
    (
        "Web search who-is",
        "Who is Yann LeCun?",
        lambda r: (
            _any_tool(r, "research_web_search") and _no_pending(r),
            "expected research_web_search direct-executed",
        ),
    ),
    (
        "Weather default",
        "What's the weather like right now?",
        lambda r: (
            _any_tool(r, "research_weather") and _has_result(r, "research_weather") and _no_pending(r),
            "expected research_weather with a result, no pending",
        ),
    ),
    (
        "Weather city",
        "What's the temperature in Mumbai right now?",
        lambda r: (
            _any_tool(r, "research_weather"),
            "expected research_weather (city not strictly required for pass)",
        ),
    ),
    (
        "News default",
        "What's the latest news headlines?",
        lambda r: (
            _any_tool(r, "research_news") and _no_pending(r),
            "expected research_news direct-executed",
        ),
    ),
    (
        "Currency convert",
        "Convert 100 USD to INR",
        lambda r: (
            _any_tool(r, "research_currency_convert") and _no_pending(r),
            "expected research_currency_convert direct-executed",
        ),
    ),
    (
        "Currency natural phrasing",
        "How much is 50 dollars in rupees?",
        lambda r: (
            _any_tool(r, "research_currency_convert"),
            "expected research_currency_convert from natural phrasing",
        ),
    ),
    (
        "Stock single ticker",
        "What's the AAPL stock price today?",
        lambda r: (
            _any_tool(r, "research_stock_price") and _no_pending(r),
            "expected research_stock_price direct-executed",
        ),
    ),
    (
        "Sports score",
        "Latest IPL match score",
        lambda r: (
            _any_tool(r, "research_sports_score") and _no_pending(r),
            "expected research_sports_score direct-executed",
        ),
    ),
    (
        "Deep research shallow default",
        "Deep research the impact of LLMs on education",
        lambda r: (
            _any_tool(r, "research_deep_research") and _no_pending(r),
            "expected research_deep_research direct-executed",
        ),
    ),
    (
        "Tell me about (web search)",
        "Tell me about the Apollo 11 mission",
        lambda r: (
            _any_tool(r, "research_web_search") and _no_pending(r),
            "expected research_web_search (general factual lookup)",
        ),
    ),
    (
        "Calendar question stays on calendar (priority check)",
        "What's on my calendar today?",
        lambda r: (
            _any_tool(r, "calendar_list_upcoming"),
            "calendar-y phrasing must still route to calendar_agent, not research_agent",
        ),
    ),
]


def run(base_url: str, token: str, delay: float = 1.5) -> int:
    intent_url = base_url.rstrip("/") + "/api/assistant/intent"
    print(f"\n{'Research Agent - HTTP Smoke Test':^90}")
    print(f"{'URL: ' + intent_url:^90}")
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
                txt = (resp.get("assistant_message") or "")[:300]
                print(f"  tool_results: {json.dumps([{'tool': t.get('tool'), 'has_result': 'result' in t, 'has_error': 'error' in t} for t in tr], indent=2)}")
                if pa:
                    print(f"  pending_actions (UNEXPECTED for research):")
                    for p in pa:
                        print(f"    - tool_name={p.get('tool_name')} brief={p.get('brief_label')!r}")
                print(f"  assistant_text: {txt!r}")

    return 0 if not failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Research agent HTTP smoke test")
    parser.add_argument("--url", default="https://meetingboxai.lucratechsol.com", help="Base URL of the server")
    parser.add_argument("--token", required=True, help="Bearer JWT from the dashboard")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between requests")
    args = parser.parse_args()

    sys.exit(run(args.url, args.token, args.delay))
