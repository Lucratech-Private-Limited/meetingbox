"""
Unit tests for the Calendar Operations Agent (calendar_agent).

Covers:
  - calendar_agent.json schema: required fields, tool_policies vs tools alignment, triggers
  - _filter_steps_for_agent: unknown tools dropped; all 6 calendar + 2 commitment tools pass
  - assistant_action_brief_label: every calendar tool produces a non-empty label
  - Dispatch smoke (no LLM, no Google Calendar API):
      * READ tools (list, suggest_free_slots, commitment_list) execute directly
      * WRITE tools (create, update, delete, RSVP) queue for approval
      * Conflict pre-check on create surfaces overlapping events in assistant_message
        but still queues the create (per agent guideline)
      * Clarification step (planner returns {"tool":"clarify"}) surfaces the question
        and queues NOTHING
  - Service-layer extensions:
      * update_event supports new_start_time / new_duration_minutes / new_date / new_location
        / new_recurrence / attendees_remove
      * rsvp_event validates status, locates by event_id or title+date, patches user row
      * find_overlapping_events filters strict overlaps
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
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
    p = Path(__file__).resolve().parent.parent / "agents" / "calendar_agent.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _bootstrap_db(tmp_path, monkeypatch):
    db = tmp_path / f"cal_{uuid.uuid4().hex}.db"
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

class TestCalendarAgentJson:
    def test_required_top_level_fields(self):
        doc = _load_agent_json()
        for f in ("id", "name", "tools", "tool_policies", "guidelines", "memory_context"):
            assert f in doc, f"Missing required field: {f}"

    def test_id_and_name(self):
        doc = _load_agent_json()
        assert doc["id"] == "calendar_agent"
        assert "Calendar" in doc["name"]

    def test_tools_list_complete(self):
        doc = _load_agent_json()
        expected = {
            "calendar_list_upcoming",
            "calendar_suggest_free_slots",
            "calendar_create_event",
            "calendar_update_event",
            "calendar_delete_event",
            "calendar_rsvp_event",
            "commitment_list",
            "commitment_upsert",
        }
        assert set(doc["tools"]) == expected

    def test_tool_policies_match_tools(self):
        doc = _load_agent_json()
        tools = set(doc["tools"])
        policies = set(doc["tool_policies"].keys())
        # Every tool needs a policy entry
        missing = tools - policies
        extra = policies - tools
        assert not missing, f"Tools missing policy: {missing}"
        assert not extra, f"Policy keys not in tools list: {extra}"

    def test_read_tools_no_approval(self):
        doc = _load_agent_json()
        for t in ("calendar_list_upcoming", "calendar_suggest_free_slots", "commitment_list"):
            assert doc["tool_policies"][t]["requires_approval"] is False, t

    def test_write_tools_require_approval(self):
        doc = _load_agent_json()
        for t in ("calendar_create_event", "calendar_update_event", "calendar_delete_event", "calendar_rsvp_event"):
            assert doc["tool_policies"][t]["requires_approval"] is True, t

    def test_commitment_upsert_direct_execute(self):
        doc = _load_agent_json()
        # commitment_upsert is local-only state, allowed direct per existing behaviour
        assert doc["tool_policies"]["commitment_upsert"]["requires_approval"] is False

    def test_rsvp_has_safety_clause(self):
        doc = _load_agent_json()
        assert "safety" in doc["tool_policies"]["calendar_rsvp_event"]

    def test_memory_context_enabled(self):
        doc = _load_agent_json()
        assert doc.get("memory_context") is True

    def test_priority_at_least_5(self):
        doc = _load_agent_json()
        # Needs to outrank generic agents on overlapping triggers like "follow up"
        assert int(doc.get("priority", 0)) >= 5

    def test_triggers_cover_required_phrases(self):
        doc = _load_agent_json()
        trig_lower = {t.lower() for t in doc["triggers"]}
        for phrase in (
            "calendar", "schedule", "meeting", "reschedule", "rsvp",
            "block calendar", "focus time", "delete event", "cancel the meeting",
            "add attendee", "remove attendee", "accept invite", "decline invite",
            "email everyone", "notify attendees",
        ):
            assert phrase in trig_lower, f"Missing trigger: {phrase}"


# ---------------------------------------------------------------------------
# 2. _filter_steps_for_agent
# ---------------------------------------------------------------------------

class TestFilterStepsForAgent:
    def test_all_calendar_tools_kept(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        steps = [{"tool": t, "args": {}} for t in (
            "calendar_list_upcoming",
            "calendar_suggest_free_slots",
            "calendar_create_event",
            "calendar_update_event",
            "calendar_delete_event",
            "calendar_rsvp_event",
            "commitment_list",
            "commitment_upsert",
        )]
        out = asvc._filter_steps_for_agent("calendar_agent", steps)
        assert {s["tool"] for s in out} == {s["tool"] for s in steps}

    def test_unknown_tool_dropped(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        out = asvc._filter_steps_for_agent("calendar_agent", [
            {"tool": "calendar_list_upcoming"},
            {"tool": "gmail_send_email"},
            {"tool": "made_up_tool"},
        ])
        kept = {s["tool"] for s in out}
        assert "calendar_list_upcoming" in kept
        assert "gmail_send_email" not in kept
        assert "made_up_tool" not in kept


# ---------------------------------------------------------------------------
# 3. Brief labels
# ---------------------------------------------------------------------------

class TestBriefLabels:
    def test_create_label(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        lbl = asvc.assistant_action_brief_label(
            "calendar_create_event",
            {"title": "Standup", "start_time": "2026-05-26T15:00:00"},
        )
        assert "Standup" in lbl and "2026-05-26" in lbl

    def test_delete_label(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        lbl = asvc.assistant_action_brief_label(
            "calendar_delete_event",
            {"title": "Focus Time", "date": "2026-05-26"},
        )
        assert "Delete" in lbl and "Focus Time" in lbl

    def test_update_attendees_label(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        lbl = asvc.assistant_action_brief_label(
            "calendar_update_event",
            {"title": "Catch Up", "attendees_add": ["a@x.com", "b@x.com"]},
        )
        assert "Catch Up" in lbl and "a@x.com" in lbl

    def test_update_reschedule_label(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        lbl = asvc.assistant_action_brief_label(
            "calendar_update_event",
            {"title": "Catch Up", "new_start_time": "2026-05-27T16:00:00", "new_duration_minutes": 45},
        )
        assert "Catch Up" in lbl and "move to" in lbl and "45m" in lbl

    def test_update_remove_attendee_label(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        lbl = asvc.assistant_action_brief_label(
            "calendar_update_event",
            {"title": "Catch Up", "attendees_remove": ["a@x.com"]},
        )
        assert "Catch Up" in lbl and "remove" in lbl and "a@x.com" in lbl

    def test_rsvp_accept_label(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        lbl = asvc.assistant_action_brief_label(
            "calendar_rsvp_event",
            {"title": "Team Meeting", "date": "2026-05-26", "response_status": "accepted"},
        )
        assert "Accept" in lbl and "Team Meeting" in lbl

    def test_rsvp_decline_label(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        lbl = asvc.assistant_action_brief_label(
            "calendar_rsvp_event",
            {"title": "Team Meeting", "response_status": "declined"},
        )
        assert "Decline" in lbl and "Team Meeting" in lbl


# ---------------------------------------------------------------------------
# 4. Dispatch — READ tools execute directly (no queue)
# ---------------------------------------------------------------------------

class TestDispatchRead:
    def test_list_upcoming_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        fake_events = {"events": [{"id": "e1", "summary": "Team Sync"}], "count": 1}
        with patch.object(asvc, "calendar_list_upcoming", return_value=fake_events) as m_list, \
             patch.object(asvc, "plan_calendar_steps", return_value=[
                {"tool": "calendar_list_upcoming", "args": {"max_results": 10}, "is_write": False}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="calendar_agent", method="triggers")):
            r = asvc.process_assistant_intent(message="what's on my calendar today?", user_id=uid, meeting_id=None)
        m_list.assert_called_once()
        assert any(t.get("tool") == "calendar_list_upcoming" and "result" in t for t in r["tool_results"]), r
        assert not r["pending_actions"], "list should not queue anything"

    def test_suggest_free_slots_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        fake_slots = {"slots": [{"start": "2026-05-27T10:00:00"}], "count": 1}
        with patch.object(asvc, "calendar_suggest_free_slots", return_value=fake_slots), \
             patch.object(asvc, "plan_calendar_steps", return_value=[
                {"tool": "calendar_suggest_free_slots", "args": {"days_ahead": 5, "duration_minutes": 30}, "is_write": False}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="calendar_agent", method="triggers")):
            r = asvc.process_assistant_intent(message="when am I free this week?", user_id=uid, meeting_id=None)
        assert any(t.get("tool") == "calendar_suggest_free_slots" and "result" in t for t in r["tool_results"])
        assert not r["pending_actions"]


# ---------------------------------------------------------------------------
# 5. Dispatch — WRITE tools queue for approval
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool,args,label_contains", [
    ("calendar_create_event",
     {"title": "Focus", "start_time": "2026-05-27T15:00:00", "duration_minutes": 30, "attendees": [], "timezone": "Asia/Kolkata"},
     "Focus"),
    ("calendar_update_event",
     {"title": "Catch Up", "date": "2026-05-27", "attendees_add": ["new@x.com"]},
     "Catch Up"),
    ("calendar_update_event",
     {"title": "Catch Up", "new_start_time": "2026-05-27T16:00:00", "new_duration_minutes": 60},
     "move to"),
    ("calendar_delete_event",
     {"title": "Cancel Me", "date": "2026-05-27"},
     "Delete"),
    ("calendar_rsvp_event",
     {"title": "Team All Hands", "date": "2026-05-27", "response_status": "accepted"},
     "Accept"),
])
class TestDispatchQueue:
    def test_tool_is_queued_not_executed(self, tmp_path, monkeypatch, tool, args, label_contains):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        with patch.object(asvc, "plan_calendar_steps", return_value=[
                {"tool": tool, "args": args, "is_write": True}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="calendar_agent", method="triggers")), \
             patch.object(asvc, "calendar_check_conflicts", return_value=[]):
            r = asvc.process_assistant_intent(message="task message", user_id=uid, meeting_id=None)

        assert any(p.get("tool_name") == tool for p in r["pending_actions"]), \
            f"{tool} should produce a pending action; got {r['pending_actions']}"
        # confirm the tool was NOT executed inline
        tool_row = next((t for t in r["tool_results"] if t.get("tool") == tool), None)
        assert tool_row is not None
        assert tool_row.get("queued") is True
        # brief label is set
        meta = next(p for p in r["pending_actions"] if p.get("tool_name") == tool)
        assert label_contains in str(meta.get("brief_label") or "")

    def test_no_underlying_api_called(self, tmp_path, monkeypatch, tool, args, label_contains):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        # If any *_from_payload were called during the planning turn, that would mean we executed
        # a write without approval — must never happen.
        with patch.object(asvc, "plan_calendar_steps", return_value=[
                {"tool": tool, "args": args, "is_write": True}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="calendar_agent", method="triggers")), \
             patch.object(asvc, "calendar_check_conflicts", return_value=[]), \
             patch.object(asvc, "calendar_create_from_payload") as m_create, \
             patch.object(asvc, "calendar_update_from_payload") as m_upd, \
             patch.object(asvc, "calendar_delete_from_payload") as m_del, \
             patch.object(asvc, "calendar_rsvp_from_payload") as m_rsvp:
            asvc.process_assistant_intent(message="task message", user_id=uid, meeting_id=None)
        for m in (m_create, m_upd, m_del, m_rsvp):
            m.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Conflict pre-check on create
# ---------------------------------------------------------------------------

class TestConflictPrecheck:
    def test_overlap_surfaced_in_assistant_message_but_create_still_queued(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        conflicts = [{
            "id": "conf1",
            "summary": "Product Review",
            "start_local": "2:00 PM",
            "end_local": "3:00 PM",
            "date_local": "2026-05-27",
        }]
        create_args = {
            "title": "Quick Sync",
            "start_time": "2026-05-27T14:30:00",
            "duration_minutes": 30,
            "attendees": [],
            "timezone": "Asia/Kolkata",
        }
        with patch.object(asvc, "plan_calendar_steps", return_value=[
                {"tool": "calendar_create_event", "args": create_args, "is_write": True}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="calendar_agent", method="triggers")), \
             patch.object(asvc, "calendar_check_conflicts", return_value=conflicts):
            r = asvc.process_assistant_intent(message="schedule quick sync at 2:30", user_id=uid, meeting_id=None)

        msg = (r.get("assistant_message") or "").lower()
        assert "overlap" in msg or "heads up" in msg, f"Conflict not surfaced in: {msg}"
        assert "product review" in msg, f"Conflict event name not surfaced in: {msg}"
        assert any(p.get("tool_name") == "calendar_create_event" for p in r["pending_actions"]), "Create should still be queued"

    def test_no_overlap_no_warning(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        create_args = {
            "title": "Quick Sync",
            "start_time": "2026-05-27T14:30:00",
            "duration_minutes": 30,
            "attendees": [],
        }
        with patch.object(asvc, "plan_calendar_steps", return_value=[
                {"tool": "calendar_create_event", "args": create_args, "is_write": True}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="calendar_agent", method="triggers")), \
             patch.object(asvc, "calendar_check_conflicts", return_value=[]):
            r = asvc.process_assistant_intent(message="schedule quick sync at 2:30", user_id=uid, meeting_id=None)

        msg = (r.get("assistant_message") or "").lower()
        assert "overlap" not in msg, f"Spurious conflict warning in: {msg}"


# ---------------------------------------------------------------------------
# 7. Clarification step (missing required fields)
# ---------------------------------------------------------------------------

class TestClarification:
    def test_clarify_step_produces_question_no_queue(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        with patch.object(asvc, "plan_calendar_steps", return_value=[
                {"tool": "clarify", "args": {"question": "What time should we block?", "missing_field": "start_time"}, "is_write": False}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="calendar_agent", method="triggers")):
            r = asvc.process_assistant_intent(message="block 30 mins for focus", user_id=uid, meeting_id=None)

        assert not r["pending_actions"], "Clarification must not queue anything"
        assert "What time" in (r.get("assistant_message") or "")


# ---------------------------------------------------------------------------
# 8. Service layer extensions (no live Google API; mock the discovery client)
# ---------------------------------------------------------------------------

class TestServiceExtensions:
    def test_update_event_supports_reschedule(self):
        from services.calendar import update_event
        # Existing event: 2026-05-27 10:00-10:30 IST
        existing = {
            "id": "evt1",
            "summary": "Catch Up",
            "start": {"dateTime": "2026-05-27T10:00:00+05:30", "timeZone": "Asia/Kolkata"},
            "end":   {"dateTime": "2026-05-27T10:30:00+05:30", "timeZone": "Asia/Kolkata"},
            "attendees": [{"email": "a@x.com"}, {"email": "b@x.com"}],
        }

        events_resource = MagicMock()
        events_resource.get.return_value.execute.return_value = existing
        patched_event = {**existing, "summary": "Catch Up", "start": {"dateTime": "2026-05-27T16:00:00+05:30"}}
        events_resource.patch.return_value.execute.return_value = patched_event

        svc = MagicMock()
        svc.events.return_value = events_resource

        with patch("services.calendar.build", return_value=svc):
            update_event(
                credentials=MagicMock(),
                event_id="evt1",
                new_start_time="2026-05-27T16:00:00",
                new_duration_minutes=60,
                timezone="Asia/Kolkata",
            )
        call_kwargs = events_resource.patch.call_args.kwargs
        body = call_kwargs["body"]
        assert "start" in body and "16:00:00" in body["start"]["dateTime"]
        # 60-min duration
        assert "17:00:00" in body["end"]["dateTime"]

    def test_update_event_attendees_remove(self):
        from services.calendar import update_event
        existing = {
            "id": "evt1",
            "summary": "Catch Up",
            "start": {"dateTime": "2026-05-27T10:00:00+05:30", "timeZone": "Asia/Kolkata"},
            "end":   {"dateTime": "2026-05-27T10:30:00+05:30", "timeZone": "Asia/Kolkata"},
            "attendees": [{"email": "keep@x.com"}, {"email": "drop@x.com"}],
        }
        events_resource = MagicMock()
        events_resource.get.return_value.execute.return_value = existing
        events_resource.patch.return_value.execute.return_value = existing
        svc = MagicMock()
        svc.events.return_value = events_resource
        with patch("services.calendar.build", return_value=svc):
            update_event(
                credentials=MagicMock(),
                event_id="evt1",
                attendees_remove=["drop@x.com"],
            )
        body = events_resource.patch.call_args.kwargs["body"]
        emails = {a["email"] for a in body["attendees"]}
        assert emails == {"keep@x.com"}

    def test_rsvp_invalid_status_rejected(self):
        from services.calendar import rsvp_event
        with pytest.raises(ValueError):
            rsvp_event(credentials=MagicMock(), event_id="evt1", response_status="banana")

    def test_rsvp_locates_self_and_patches(self):
        from services.calendar import rsvp_event
        existing = {
            "id": "evt1",
            "summary": "Team Meeting",
            "start": {"dateTime": "2026-05-27T10:00:00+05:30"},
            "end":   {"dateTime": "2026-05-27T10:30:00+05:30"},
            "attendees": [
                {"email": "organizer@x.com", "organizer": True},
                {"email": "me@x.com", "self": True, "responseStatus": "needsAction"},
                {"email": "other@x.com"},
            ],
        }
        events_resource = MagicMock()
        events_resource.get.return_value.execute.return_value = existing
        events_resource.patch.return_value.execute.return_value = existing
        cal_resource = MagicMock()
        cal_resource.get.return_value.execute.return_value = {"id": "me@x.com"}
        svc = MagicMock()
        svc.events.return_value = events_resource
        svc.calendars.return_value = cal_resource
        with patch("services.calendar.build", return_value=svc):
            r = rsvp_event(credentials=MagicMock(), event_id="evt1", response_status="declined")
        assert r["response_status"] == "declined"
        body = events_resource.patch.call_args.kwargs["body"]
        me_row = next(a for a in body["attendees"] if a.get("self") is True)
        assert me_row["responseStatus"] == "declined"

    def test_rsvp_user_not_an_attendee(self):
        from services.calendar import rsvp_event
        existing = {
            "id": "evt1",
            "summary": "Solo Focus",
            "start": {"dateTime": "2026-05-27T10:00:00+05:30"},
            "end":   {"dateTime": "2026-05-27T10:30:00+05:30"},
            "attendees": [{"email": "someone-else@x.com"}],
        }
        events_resource = MagicMock()
        events_resource.get.return_value.execute.return_value = existing
        cal_resource = MagicMock()
        cal_resource.get.return_value.execute.return_value = {"id": "me@x.com"}
        svc = MagicMock()
        svc.events.return_value = events_resource
        svc.calendars.return_value = cal_resource
        with patch("services.calendar.build", return_value=svc), \
             pytest.raises(ValueError, match="don't appear to be an attendee"):
            rsvp_event(credentials=MagicMock(), event_id="evt1", response_status="accepted")

    def test_find_overlapping_events_strict_overlap_only(self):
        from services.calendar import find_overlapping_events
        # Three calendar events:
        # - 13:00-13:30 (ends at proposed start, NOT overlapping)
        # - 14:15-14:45 (overlaps proposed 14:00-14:30, returned)
        # - 15:00-15:30 (after proposed end, NOT overlapping)
        events = [
            {"id": "a", "summary": "Earlier",
             "start": {"dateTime": "2026-05-27T13:00:00+05:30"},
             "end":   {"dateTime": "2026-05-27T13:30:00+05:30"}},
            {"id": "b", "summary": "Overlap",
             "start": {"dateTime": "2026-05-27T14:15:00+05:30"},
             "end":   {"dateTime": "2026-05-27T14:45:00+05:30"}},
            {"id": "c", "summary": "Later",
             "start": {"dateTime": "2026-05-27T15:00:00+05:30"},
             "end":   {"dateTime": "2026-05-27T15:30:00+05:30"}},
        ]
        events_resource = MagicMock()
        events_resource.list.return_value.execute.return_value = {"items": events}
        svc = MagicMock()
        svc.events.return_value = events_resource
        with patch("services.calendar.build", return_value=svc):
            res = find_overlapping_events(
                credentials=MagicMock(),
                start_time="2026-05-27T14:00:00",
                duration_minutes=30,
                timezone="Asia/Kolkata",
            )
        ids = {e["id"] for e in res}
        assert ids == {"b"}, f"Expected only 'b' to overlap, got {ids}"

    def test_create_event_default_meet_link_when_attendees_present(self):
        from services.calendar import create_event
        events_resource = MagicMock()
        events_resource.insert.return_value.execute.return_value = {"id": "new1", "summary": "Sync"}
        svc = MagicMock()
        svc.events.return_value = events_resource
        with patch("services.calendar.build", return_value=svc):
            create_event(
                credentials=MagicMock(),
                title="Sync",
                start_time="2026-05-27T15:00:00",
                duration_minutes=30,
                attendees=["a@x.com"],
                timezone="Asia/Kolkata",
            )
        kwargs = events_resource.insert.call_args.kwargs
        assert kwargs.get("conferenceDataVersion") == 1
        body = kwargs["body"]
        assert "conferenceData" in body
        assert body["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"] == "hangoutsMeet"

    def test_create_event_no_meet_link_for_solo_block(self):
        from services.calendar import create_event
        events_resource = MagicMock()
        events_resource.insert.return_value.execute.return_value = {"id": "new2", "summary": "Focus"}
        svc = MagicMock()
        svc.events.return_value = events_resource
        with patch("services.calendar.build", return_value=svc):
            create_event(
                credentials=MagicMock(),
                title="Focus",
                start_time="2026-05-27T15:00:00",
                duration_minutes=30,
                attendees=[],
                timezone="Asia/Kolkata",
            )
        kwargs = events_resource.insert.call_args.kwargs
        assert kwargs.get("conferenceDataVersion", 0) == 0 or kwargs.get("conferenceDataVersion") is None
        body = kwargs["body"]
        assert "conferenceData" not in body

    def test_create_event_explicit_no_meet_link_override(self):
        from services.calendar import create_event
        events_resource = MagicMock()
        events_resource.insert.return_value.execute.return_value = {"id": "new3", "summary": "Sync"}
        svc = MagicMock()
        svc.events.return_value = events_resource
        with patch("services.calendar.build", return_value=svc):
            create_event(
                credentials=MagicMock(),
                title="Sync",
                start_time="2026-05-27T15:00:00",
                duration_minutes=30,
                attendees=["a@x.com"],
                add_meet_link=False,
                timezone="Asia/Kolkata",
            )
        body = events_resource.insert.call_args.kwargs["body"]
        assert "conferenceData" not in body
