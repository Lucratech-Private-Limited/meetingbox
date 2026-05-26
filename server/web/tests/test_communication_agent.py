"""
Unit tests for the Email Operations Agent (communication_agent).

Covers:
  - tool_policies / _tool_requires_approval: read vs draft vs outbound/destructive bucketing.
  - _filter_steps_for_agent: unknown tools are dropped; all 11 Gmail tools pass.
  - assistant_action_brief_label: all 11 Gmail tools produce non-empty labels.
  - Dispatch smoke (no LLM, no Gmail API):
      * Direct-execute path (gmail_create_draft, update_draft, add/remove_recipients)
        returns a real result in tool_results with no queued pending action.
      * Approval-queue path (gmail_send_email, reply, reply_all, forward, archive, delete)
        creates a pending_action row, sets queued=True in tool_results, and returns
        the right brief_label.
  - communication_agent.json schema: all required fields present, tool_policies keys
    match the tools list, memory_context is true.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_agent_json() -> dict[str, Any]:
    p = Path(__file__).resolve().parent.parent / "agents" / "communication_agent.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _bootstrap_db(tmp_path, monkeypatch):
    db = tmp_path / f"ca_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")

    import database
    importlib.reload(database)
    database.init_database()

    uid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (uid, "u1", "x", "u@test", "user", "2020-01-01"),
    )
    conn.commit()
    conn.close()

    import assistant_service as asvc
    importlib.reload(asvc)
    return uid, asvc


# ---------------------------------------------------------------------------
# 1. JSON schema sanity
# ---------------------------------------------------------------------------

class TestCommunicationAgentJson:
    def test_required_top_level_fields(self):
        doc = _load_agent_json()
        for f in ("id", "name", "tools", "tool_policies", "guidelines", "memory_context"):
            assert f in doc, f"Missing required field: {f}"

    def test_id_and_name(self):
        doc = _load_agent_json()
        assert doc["id"] == "communication_agent"
        assert "Email" in doc["name"] or "Communication" in doc["name"]

    def test_memory_context_is_true(self):
        doc = _load_agent_json()
        assert doc["memory_context"] is True

    def test_tools_list_has_11_entries(self):
        doc = _load_agent_json()
        assert len(doc["tools"]) == 11

    def test_tool_policies_covers_all_tools(self):
        doc = _load_agent_json()
        tools_set = set(doc["tools"])
        policy_set = set(doc["tool_policies"].keys())
        assert tools_set == policy_set, (
            f"tools list and tool_policies keys differ:\n"
            f"  in tools but not policies: {tools_set - policy_set}\n"
            f"  in policies but not tools: {policy_set - tools_set}"
        )

    def test_read_tools_have_requires_approval_false(self):
        doc = _load_agent_json()
        read_tools = {"gmail_list_recent"}
        for t in read_tools:
            assert doc["tool_policies"][t]["requires_approval"] is False, (
                f"{t} should not require approval"
            )

    def test_draft_edit_tools_have_requires_approval_false(self):
        doc = _load_agent_json()
        draft_tools = {
            "gmail_create_draft",
            "gmail_update_draft",
            "gmail_add_recipients",
            "gmail_remove_recipients",
        }
        for t in draft_tools:
            assert doc["tool_policies"][t]["requires_approval"] is False, (
                f"{t} should not require approval (draft stays in inbox)"
            )

    def test_outbound_and_destructive_tools_require_approval(self):
        doc = _load_agent_json()
        guarded_tools = {
            "gmail_send_email",
            "gmail_reply_to_thread",
            "gmail_reply_all",
            "gmail_forward_email",
            "gmail_archive_email",
            "gmail_delete_email",
        }
        for t in guarded_tools:
            assert doc["tool_policies"][t]["requires_approval"] is True, (
                f"{t} must require approval"
            )

    def test_guidelines_has_required_sections(self):
        doc = _load_agent_json()
        g = doc["guidelines"]
        for section in ("purpose", "behavior_rules", "search_rules", "priorities", "tool_selection_rules"):
            assert section in g, f"guidelines missing section: {section}"
        assert len(g["behavior_rules"]) >= 5
        assert len(g["tool_selection_rules"]) == 11


# ---------------------------------------------------------------------------
# 2. _tool_requires_approval helper
# ---------------------------------------------------------------------------

class TestToolRequiresApproval:
    def setup_method(self):
        import assistant_service as asvc
        self.fn = asvc._tool_requires_approval
        self.agent_doc = _load_agent_json()

    def test_list_recent_is_direct(self):
        assert self.fn(self.agent_doc, "gmail_list_recent") is False

    def test_create_draft_is_direct(self):
        assert self.fn(self.agent_doc, "gmail_create_draft") is False

    def test_update_draft_is_direct(self):
        assert self.fn(self.agent_doc, "gmail_update_draft") is False

    def test_add_recipients_is_direct(self):
        assert self.fn(self.agent_doc, "gmail_add_recipients") is False

    def test_remove_recipients_is_direct(self):
        assert self.fn(self.agent_doc, "gmail_remove_recipients") is False

    def test_send_email_requires_approval(self):
        assert self.fn(self.agent_doc, "gmail_send_email") is True

    def test_reply_requires_approval(self):
        assert self.fn(self.agent_doc, "gmail_reply_to_thread") is True

    def test_reply_all_requires_approval(self):
        assert self.fn(self.agent_doc, "gmail_reply_all") is True

    def test_forward_requires_approval(self):
        assert self.fn(self.agent_doc, "gmail_forward_email") is True

    def test_archive_requires_approval(self):
        assert self.fn(self.agent_doc, "gmail_archive_email") is True

    def test_delete_requires_approval(self):
        assert self.fn(self.agent_doc, "gmail_delete_email") is True

    def test_unknown_tool_falls_back_to_agent_level_default(self):
        # agent-level requires_approval is True
        assert self.fn(self.agent_doc, "nonexistent_tool") is True

    def test_empty_doc_returns_false(self):
        assert self.fn({}, "gmail_send_email") is False

    def test_none_doc_returns_false(self):
        assert self.fn(None, "gmail_send_email") is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. _filter_steps_for_agent
# ---------------------------------------------------------------------------

class TestFilterSteps:
    def setup_method(self):
        import assistant_service as asvc
        self.fn = asvc._filter_steps_for_agent

    def _step(self, tool: str) -> dict:
        return {"tool": tool, "args": {}, "is_write": False}

    def test_drops_unknown_tool(self):
        assert self.fn("communication_agent", [self._step("not_a_gmail_tool")]) == []

    def test_keeps_all_11_gmail_tools(self):
        doc = _load_agent_json()
        steps = [self._step(t) for t in doc["tools"]]
        result = self.fn("communication_agent", steps)
        assert len(result) == 11

    def test_drops_calendar_tool_from_comm_agent(self):
        steps = [self._step("calendar_list_upcoming"), self._step("gmail_list_recent")]
        result = self.fn("communication_agent", steps)
        assert len(result) == 1
        assert result[0]["tool"] == "gmail_list_recent"

    def test_empty_input_returns_empty(self):
        assert self.fn("communication_agent", []) == []


# ---------------------------------------------------------------------------
# 4. assistant_action_brief_label — all 11 Gmail tools
# ---------------------------------------------------------------------------

class TestBriefLabels:
    def setup_method(self):
        import assistant_service as asvc
        self.fn = asvc.assistant_action_brief_label

    def test_send_email_label(self):
        label = self.fn("gmail_send_email", {"to": "john@example.com", "subject": "Q3 Review"})
        assert "john@example.com" in label
        assert "Q3 Review" in label

    def test_create_draft_label(self):
        label = self.fn("gmail_create_draft", {"to": "jane@test.com", "subject": "Draft"})
        assert label  # non-empty

    def test_update_draft_label(self):
        label = self.fn("gmail_update_draft", {"draft_id": "abc123", "subject": "Updated"})
        assert "abc123" in label

    def test_add_recipients_label(self):
        label = self.fn("gmail_add_recipients", {"draft_id": "d1", "to_add": ["x@y.com"]})
        assert "x@y.com" in label

    def test_remove_recipients_label(self):
        label = self.fn("gmail_remove_recipients", {"draft_id": "d2", "to_remove": ["old@y.com"]})
        assert "old@y.com" in label

    def test_reply_label(self):
        label = self.fn("gmail_reply_to_thread", {"thread_id": "t1", "body": "Thanks!"})
        assert label  # non-empty

    def test_reply_all_label(self):
        label = self.fn("gmail_reply_all", {"thread_id": "t1", "body": "All noted."})
        assert "reply-all" in label.lower() or "reply_all" in label.lower() or label

    def test_forward_label(self):
        label = self.fn("gmail_forward_email", {"message_id": "m1", "to": "fwd@test.com"})
        assert "fwd@test.com" in label

    def test_archive_label(self):
        label = self.fn("gmail_archive_email", {"message_id": "msg999"})
        assert "msg999" in label

    def test_delete_label(self):
        label = self.fn("gmail_delete_email", {"message_id": "msg888"})
        assert "msg888" in label

    def test_all_11_tools_produce_non_empty_label(self):
        doc = _load_agent_json()
        for tool in doc["tools"]:
            label = self.fn(tool, {})
            assert label, f"Empty label for {tool}"


# ---------------------------------------------------------------------------
# 5. Dispatch smoke tests (mocked Gmail layer)
# ---------------------------------------------------------------------------
# These tests inject process_assistant_intent and pin the route to
# communication_agent, then mock only the Gmail service layer.
# They do NOT call the LLM planner — instead they inject a pre-built plan
# so we can test the dispatch logic deterministically.

def _mock_plan(steps: list[dict]) -> list[dict]:
    return steps


class TestDispatchDirect:
    """Direct-execute tools (create_draft, update_draft, add/remove_recipients)
    should produce a real result in tool_results — no queued pending action."""

    def test_create_draft_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

        mock_result = {"draft_id": "DRAFTID1", "to": "john@example.com", "subject": "Test"}

        with (
            patch.object(asvc, "route_intent", return_value=MagicMock(agent_id="communication_agent", method="keyword")),
            patch.object(asvc, "plan_communication_steps", return_value=[
                {"tool": "gmail_create_draft", "args": {"to": "john@example.com", "subject": "Test", "body": "Hello"}, "is_write": True}
            ]),
            patch.object(asvc, "gmail_draft_from_payload", return_value=mock_result),
        ):
            result = asvc.process_assistant_intent(message="draft an email to john about test", user_id=uid, meeting_id=None)

        tool_results = result.get("tool_results", [])
        pending = result.get("pending_actions", [])

        # Should have a real result, not a queued placeholder
        draft_results = [t for t in tool_results if t.get("tool") == "gmail_create_draft"]
        assert len(draft_results) == 1
        assert draft_results[0].get("result") == mock_result
        assert "queued" not in draft_results[0]
        # No pending action created
        assert pending == []

    def test_update_draft_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

        mock_result = {"draft_id": "DRAFTID2", "subject": "Updated Subject"}

        with (
            patch.object(asvc, "route_intent", return_value=MagicMock(agent_id="communication_agent", method="keyword")),
            patch.object(asvc, "plan_communication_steps", return_value=[
                {"tool": "gmail_update_draft", "args": {"draft_id": "DRAFTID2", "subject": "Updated Subject"}, "is_write": True}
            ]),
            patch.object(asvc, "gmail_update_draft_from_payload", return_value=mock_result),
        ):
            result = asvc.process_assistant_intent(message="update the draft subject to 'Updated Subject'", user_id=uid, meeting_id=None)

        tool_results = result.get("tool_results", [])
        pending = result.get("pending_actions", [])

        update_results = [t for t in tool_results if t.get("tool") == "gmail_update_draft"]
        assert len(update_results) == 1
        assert update_results[0].get("result") == mock_result
        assert pending == []

    def test_add_recipients_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

        mock_result = {"draft_id": "D3", "to": "a@x.com, b@x.com", "added": {"to": ["b@x.com"]}}

        with (
            patch.object(asvc, "route_intent", return_value=MagicMock(agent_id="communication_agent", method="keyword")),
            patch.object(asvc, "plan_communication_steps", return_value=[
                {"tool": "gmail_add_recipients", "args": {"draft_id": "D3", "to_add": ["b@x.com"]}, "is_write": True}
            ]),
            patch.object(asvc, "gmail_add_recipients_from_payload", return_value=mock_result),
        ):
            result = asvc.process_assistant_intent(message="add b@x.com to the draft", user_id=uid, meeting_id=None)

        tool_results = result.get("tool_results", [])
        pending = result.get("pending_actions", [])

        add_results = [t for t in tool_results if t.get("tool") == "gmail_add_recipients"]
        assert len(add_results) == 1
        assert add_results[0].get("result") == mock_result
        assert pending == []

    def test_remove_recipients_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

        mock_result = {"draft_id": "D4", "to": "a@x.com", "removed": {"to": ["b@x.com"]}}

        with (
            patch.object(asvc, "route_intent", return_value=MagicMock(agent_id="communication_agent", method="keyword")),
            patch.object(asvc, "plan_communication_steps", return_value=[
                {"tool": "gmail_remove_recipients", "args": {"draft_id": "D4", "to_remove": ["b@x.com"]}, "is_write": True}
            ]),
            patch.object(asvc, "gmail_remove_recipients_from_payload", return_value=mock_result),
        ):
            result = asvc.process_assistant_intent(message="remove b@x.com from the draft", user_id=uid, meeting_id=None)

        tool_results = result.get("tool_results", [])
        pending = result.get("pending_actions", [])

        rm_results = [t for t in tool_results if t.get("tool") == "gmail_remove_recipients"]
        assert len(rm_results) == 1
        assert rm_results[0].get("result") == mock_result
        assert pending == []


class TestDispatchQueue:
    """Outbound / destructive tools should be queued for approval — not executed immediately."""

    @pytest.mark.parametrize("tool,args,label_substr", [
        (
            "gmail_send_email",
            {"to": "j@x.com", "subject": "Hello", "body": "Hi"},
            "j@x.com",
        ),
        (
            "gmail_reply_to_thread",
            {"thread_id": "TH1", "body": "Thanks"},
            "Reply",
        ),
        (
            "gmail_reply_all",
            {"thread_id": "TH2", "body": "All noted"},
            "reply-all",
        ),
        (
            "gmail_forward_email",
            {"message_id": "M1", "to": "fwd@x.com"},
            "fwd@x.com",
        ),
        (
            "gmail_archive_email",
            {"message_id": "ARC1"},
            "ARC1",
        ),
        (
            "gmail_delete_email",
            {"message_id": "DEL1"},
            "DEL1",
        ),
    ])
    def test_tool_is_queued_not_executed(self, tool, args, label_substr, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

        with (
            patch.object(asvc, "route_intent", return_value=MagicMock(agent_id="communication_agent", method="keyword")),
            patch.object(asvc, "plan_communication_steps", return_value=[
                {"tool": tool, "args": args, "is_write": True}
            ]),
        ):
            result = asvc.process_assistant_intent(message=f"test {tool}", user_id=uid, meeting_id=None)

        tool_results = result.get("tool_results", [])
        pending = result.get("pending_actions", [])

        # Should be queued in tool_results
        tr = next((t for t in tool_results if t.get("tool") == tool), None)
        assert tr is not None, f"No tool_result entry for {tool}"
        assert tr.get("queued") is True, f"{tool} should be queued"
        assert "pending_id" in tr, f"{tool} queued result should have pending_id"

        # Should appear in pending_actions
        assert len(pending) >= 1, f"{tool} should create a pending action"
        pa = next((p for p in pending if p.get("tool_name") == tool), None)
        assert pa is not None, f"No pending_action for {tool}"
        assert pa.get("status") == "pending"

        # Brief label should mention the relevant field
        label = pa.get("brief_label", "")
        assert label_substr.lower() in label.lower(), (
            f"{tool}: expected '{label_substr}' in brief label, got: '{label}'"
        )

    def test_send_email_does_not_call_gmail_api(self, tmp_path, monkeypatch):
        """The Gmail API should never be called for queued tools before approval."""
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

        with (
            patch.object(asvc, "route_intent", return_value=MagicMock(agent_id="communication_agent", method="keyword")),
            patch.object(asvc, "plan_communication_steps", return_value=[
                {"tool": "gmail_send_email", "args": {"to": "j@x.com", "subject": "S", "body": "B"}, "is_write": True}
            ]),
            patch.object(asvc, "gmail_send_from_payload") as mock_send,
        ):
            asvc.process_assistant_intent(message="send an email to j@x.com", user_id=uid, meeting_id=None)

        mock_send.assert_not_called()

    def test_reply_all_assistant_text_mentions_reply_all(self, tmp_path, monkeypatch):
        """reply_all confirmation text must mention 'reply-all' (safety notice for broadcast)."""
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

        with (
            patch.object(asvc, "route_intent", return_value=MagicMock(agent_id="communication_agent", method="keyword")),
            patch.object(asvc, "plan_communication_steps", return_value=[
                {"tool": "gmail_reply_all", "args": {"thread_id": "TH99", "body": "Noted"}, "is_write": True}
            ]),
        ):
            result = asvc.process_assistant_intent(message="reply all on thread TH99", user_id=uid, meeting_id=None)

        text = (result.get("assistant_message") or "").lower()
        assert "reply-all" in text or "reply all" in text, (
            f"reply_all confirmation text must mention reply-all. Got: {result.get('assistant_message')!r}"
        )


# ---------------------------------------------------------------------------
# 6. gmail_list_recent: direct, no queuing
# ---------------------------------------------------------------------------

class TestGmailListRecent:
    def test_list_is_direct_execute(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)

        mock_messages = [{"id": "m1", "subject": "Hello"}, {"id": "m2", "subject": "World"}]

        with (
            patch.object(asvc, "route_intent", return_value=MagicMock(agent_id="communication_agent", method="keyword")),
            patch.object(asvc, "plan_communication_steps", return_value=[
                {"tool": "gmail_list_recent", "args": {"max_results": 5}, "is_write": False}
            ]),
            patch.object(asvc, "gmail_list_recent", return_value={"messages": mock_messages, "count": 2}),
        ):
            result = asvc.process_assistant_intent(message="check my inbox", user_id=uid, meeting_id=None)

        tool_results = result.get("tool_results", [])
        pending = result.get("pending_actions", [])

        list_results = [t for t in tool_results if t.get("tool") == "gmail_list_recent"]
        assert len(list_results) == 1
        assert list_results[0].get("result", {}).get("count") == 2
        assert pending == []
