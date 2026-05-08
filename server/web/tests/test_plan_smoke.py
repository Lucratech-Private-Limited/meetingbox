"""Smoke tests for backend plan changes (memory scope, step filter)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.memory_tool import _scope_clause_for_user
from assistant_service import _filter_steps_for_agent


def test_scope_without_user_is_empty():
    sql, params = _scope_clause_for_user(None)
    assert "1 = 0" in sql
    assert params == []


def test_filter_steps_drops_unknown_calendar_tools():
    steps = [{"tool": "not_a_calendar_tool", "args": {}}]
    assert _filter_steps_for_agent("calendar_agent", steps) == []


def test_filter_steps_keeps_calendar_list():
    steps = [{"tool": "calendar_list_upcoming", "args": {"max_results": 5}}]
    out = _filter_steps_for_agent("calendar_agent", steps)
    assert len(out) == 1
    assert out[0]["tool"] == "calendar_list_upcoming"
