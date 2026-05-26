"""
Tests for the opt-in multi-agent planner (MEETINGBOX_MULTI_AGENT_PLANNER).

Covers:
  - Flag off (default): legacy single-agent path runs unchanged.
  - Flag on but planner returns None: silent fallback to single-agent path.
  - Flag on + single-step plan: skipped (treated as fallback), single-agent path runs.
  - Flag on + two-step plan: both specialists execute, results merge, audit recorded
    with routing_method="multi_agent" and routing_plan present.
  - Scratchpad: step 2 sees a PRIOR_RESULTS block in its message when depends_on_prior_results=True.
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid

import orchestrator


def _bootstrap_db(tmp_path, monkeypatch):
    """Create a fresh sqlite db and a user row; reload services to pick up the path."""
    db = tmp_path / f"mp_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")

    import database

    importlib.reload(database)
    database.init_database()

    uid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (uid, "u1", "x", "u@test", "user", "2020-01-01"),
    )
    conn.commit()
    conn.close()

    import assistant_service as asvc

    importlib.reload(asvc)
    return uid, asvc


def _force_route(asvc, agent_id):
    """Pin route_intent so we can exercise specific single-agent branches deterministically."""
    asvc.route_intent = lambda text, user_id=None: orchestrator.RouteResult(  # type: ignore[assignment]
        agent_id=agent_id, method="forced", rationale="test"
    )


def test_flag_off_runs_single_agent_path(tmp_path, monkeypatch):
    """Default behaviour: planner is never invoked; routing_method matches single-agent route."""
    monkeypatch.delenv("MEETINGBOX_MULTI_AGENT_PLANNER", raising=False)
    uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

    _force_route(asvc, "memory_agent")
    monkeypatch.setattr(asvc, "plan_memory_steps", lambda ctx: [])

    sentinel = {"called": False}

    def _planner_should_not_run(_text, user_id=None):
        sentinel["called"] = True
        return None

    monkeypatch.setattr(asvc, "plan_multi_agent_intent", _planner_should_not_run)

    out = asvc.process_assistant_intent(
        message="what did we discuss yesterday",
        user_id=uid,
        meeting_id=None,
        source="api",
    )
    assert sentinel["called"] is False
    assert out["routed_agent_id"] == "memory_agent"
    assert out["routing_method"] == "forced"
    assert "routing_plan" not in out


def test_flag_on_planner_none_falls_back_to_single_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETINGBOX_MULTI_AGENT_PLANNER", "1")
    uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

    _force_route(asvc, "memory_agent")
    monkeypatch.setattr(asvc, "plan_memory_steps", lambda ctx: [])
    monkeypatch.setattr(asvc, "plan_multi_agent_intent", lambda text, user_id=None: None)

    out = asvc.process_assistant_intent(
        message="what did we discuss yesterday",
        user_id=uid,
        meeting_id=None,
        source="api",
    )
    assert out["routing_method"] == "forced"
    assert out["routed_agent_id"] == "memory_agent"
    assert "routing_plan" not in out


def test_flag_on_single_step_plan_falls_back_to_single_agent(tmp_path, monkeypatch):
    """1-step plans must not engage the multi-agent runner (preserves response contract)."""
    monkeypatch.setenv("MEETINGBOX_MULTI_AGENT_PLANNER", "1")
    uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

    _force_route(asvc, "memory_agent")
    monkeypatch.setattr(asvc, "plan_memory_steps", lambda ctx: [])

    one_step = orchestrator.MultiAgentPlan(
        steps=[orchestrator.PlanStep(agent_id="memory_agent", message="m", rationale="solo")],
        method="openai",
        rationale="solo",
    )
    monkeypatch.setattr(asvc, "plan_multi_agent_intent", lambda text, user_id=None: one_step)

    out = asvc.process_assistant_intent(
        message="what did we discuss yesterday",
        user_id=uid,
        meeting_id=None,
        source="api",
    )
    assert out["routing_method"] == "forced"  # single-agent path ran
    assert "routing_plan" not in out


def test_flag_on_two_step_plan_runs_both_specialists(tmp_path, monkeypatch):
    """Two-step plan: memory_agent fetches, communication_agent drafts. Both branches must fire."""
    monkeypatch.setenv("MEETINGBOX_MULTI_AGENT_PLANNER", "1")
    uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

    # Force the legacy router away — multi_agent gate fires first.
    asvc.route_intent = lambda text, user_id=None: orchestrator.RouteResult(  # type: ignore[assignment]
        agent_id=None, method="none", rationale="not_used_in_multi"
    )

    # Memory step returns a single search call that yields a fake meeting summary.
    def _memory_plan(_ctx):
        return [{"tool": "memory_search_meetings", "args": {"query": "x"}, "is_write": False}]

    monkeypatch.setattr(asvc, "plan_memory_steps", _memory_plan)
    monkeypatch.setattr(
        asvc,
        "memory_search_meetings",
        lambda user_id, q, max_results=12: {
            "count": 1,
            "meetings": [{"id": "m1", "summary": "Hi we discussed Q3 plan"}],
        },
    )
    monkeypatch.setattr(asvc, "_synthesize_memory_reply", lambda text, results: "memory replied")

    # Communication step records the message it received so we can assert scratchpad threading.
    captured_messages: list[str] = []

    def _comm_plan(ctx):
        captured_messages.append(ctx)
        return [
            {
                "tool": "gmail_create_draft",
                "args": {"to": "john@example.com", "subject": "Q3 summary", "body": "..."},
                "is_write": True,
            }
        ]

    monkeypatch.setattr(asvc, "plan_communication_steps", _comm_plan)

    two_step = orchestrator.MultiAgentPlan(
        steps=[
            orchestrator.PlanStep(
                agent_id="memory_agent",
                message="fetch latest meeting summary",
                depends_on_prior_results=False,
                rationale="need_summary",
            ),
            orchestrator.PlanStep(
                agent_id="communication_agent",
                message="Draft an email to john@example.com with that summary",
                depends_on_prior_results=True,
                rationale="email_drafted",
            ),
        ],
        method="openai",
        rationale="memory_then_email",
    )
    monkeypatch.setattr(asvc, "plan_multi_agent_intent", lambda text, user_id=None: two_step)

    out = asvc.process_assistant_intent(
        message="email john the latest meeting summary",
        user_id=uid,
        meeting_id=None,
        source="api",
    )

    assert out["routing_method"] == "multi_agent"
    assert out["routed_agent_id"] == "memory_agent"
    assert isinstance(out.get("routing_plan"), list) and len(out["routing_plan"]) == 2

    # Memory tool ran AND communication queued a draft as a pending action.
    tools = [tr.get("tool") for tr in out["tool_results"]]
    assert "memory_search_meetings" in tools
    assert "gmail_create_draft" in tools

    pending = out["pending_actions"]
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "gmail_create_draft"

    # Every tool_result entry from the multi-agent runner gets step tags.
    assert any(tr.get("step_index") == 0 and tr.get("step_agent_id") == "memory_agent" for tr in out["tool_results"])
    assert any(
        tr.get("step_index") == 1 and tr.get("step_agent_id") == "communication_agent"
        for tr in out["tool_results"]
    )

    # Scratchpad: step 2's planner ctx must carry the PRIOR_RESULTS block.
    assert captured_messages, "communication planner should have been invoked"
    assert any("<<<PRIOR_RESULTS" in m and "PRIOR_RESULTS>>>" in m for m in captured_messages)


def test_planner_exception_falls_back_safely(tmp_path, monkeypatch):
    """If plan_multi_agent_intent raises, we must NOT crash — fall back to single-agent."""
    monkeypatch.setenv("MEETINGBOX_MULTI_AGENT_PLANNER", "1")
    uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

    _force_route(asvc, "memory_agent")
    monkeypatch.setattr(asvc, "plan_memory_steps", lambda ctx: [])

    def _boom(text, user_id=None):
        raise RuntimeError("planner LLM exploded")

    monkeypatch.setattr(asvc, "plan_multi_agent_intent", _boom)

    out = asvc.process_assistant_intent(
        message="what did we discuss",
        user_id=uid,
        meeting_id=None,
        source="api",
    )
    assert out["routing_method"] == "forced"
    assert out["routed_agent_id"] == "memory_agent"
