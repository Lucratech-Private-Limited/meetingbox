"""
Research agent plan-level smoke test.

Calls plan_research_steps() with a set of real-world messages and prints which
tool the LLM picks for each. No external HTTP is called by the planner itself.

Usage (from server/web/):
    python tests/smoke_research_plan.py

Requirements:
    ANTHROPIC_API_KEY env var must be set (Claude does the planning).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant_service import plan_research_steps, RESEARCH_TOOLS


# (label, message, expected_tool)
TEST_CASES: list[tuple[str, str, str]] = [
    # Weather
    ("Weather default",       "What's the weather today?",                                "research_weather"),
    ("Weather city",          "What's the temperature in Mumbai right now?",              "research_weather"),
    ("AQI",                   "How's the air quality outside?",                           "research_weather"),
    ("Forecast",              "What's the forecast for tomorrow?",                        "research_weather"),
    # News
    ("News default",          "What's the latest news?",                                  "research_news"),
    ("News topic",            "Any news on the AI safety bill?",                          "research_news"),
    ("Breaking news",         "What's breaking right now?",                               "research_news"),
    # Currency
    ("Currency basic",        "Convert 100 USD to INR",                                   "research_currency_convert"),
    ("Currency natural",      "How much is 50 dollars in rupees?",                        "research_currency_convert"),
    ("Currency two-way",      "What is 200 euros in dollars right now?",                  "research_currency_convert"),
    # Stock
    ("Stock single",          "What's the AAPL stock price today?",                       "research_stock_price"),
    ("Stock company",         "How is Tesla doing today?",                                "research_stock_price"),
    ("Stock NSE",             "Current share price of Reliance",                          "research_stock_price"),
    # Sports
    ("Sports live",           "What's the live score of the India vs Australia match?",   "research_sports_score"),
    ("Sports recent",         "Who won the IPL final?",                                   "research_sports_score"),
    ("Sports general",        "Latest cricket scores",                                    "research_sports_score"),
    # Deep research
    ("Deep dive",             "Do a deep dive on the impact of LLMs on education",        "research_deep_research"),
    ("Deep research",         "Deep research on the state of nuclear fusion",             "research_deep_research"),
    ("Exhaustive",            "Give me a comprehensive research summary on tariffs",      "research_deep_research"),
    # General web search (fallback)
    ("Web definition",        "What is LangChain?",                                       "research_web_search"),
    ("Web look up",           "Look up the population of Tokyo",                          "research_web_search"),
    ("Web how-to",            "How does photosynthesis work?",                            "research_web_search"),
    ("Web tell me about",     "Tell me about the Apollo 11 mission",                      "research_web_search"),
    ("Web who is",            "Who is Yann LeCun?",                                       "research_web_search"),
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

    print(f"\n{'Research Agent - Plan Smoke Test':^90}")
    print(SEPARATOR)
    print(f"{'#':<3}  {'Scenario':<22}  {'Expected':<28}  {'Got':<30}  {'OK?'}")
    print(SEPARATOR)

    results: list[tuple[int, str, str, str, list[dict[str, Any]]]] = []
    for i, (label, message, hint) in enumerate(TEST_CASES, 1):
        steps: list[dict[str, Any]] = plan_research_steps(message)
        tools_got = [_tool_of_step(s) for s in steps if _tool_of_step(s) in RESEARCH_TOOLS]

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

        results.append((i, label, hint, got_display, steps))
        print(f"{i:<3}  {label:<22}  {hint:<28}  {got_display:<30}  {mark}")

    print(SEPARATOR)
    print(f"\nResults: {passed}/{total} exact match  |  {wrong_tool} different tool  |  {no_plan} no plan\n")

    if (wrong_tool + no_plan) > 0:
        print("Details for non-passing cases:")
        print(SEPARATOR)
        for i, label, hint, got_display, steps in results:
            if got_display == "(no plan)" or hint not in got_display:
                print(f"\n[{i}] {label}")
                print(f"  Expected : {hint}")
                if steps:
                    for s in steps:
                        print(f"    tool={s.get('tool')}  args={s.get('args')}")
                else:
                    print("    (empty plan)")

    return 0 if no_plan == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
