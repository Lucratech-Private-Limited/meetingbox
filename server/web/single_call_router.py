"""
Single-call route+plan path (opt-in via MEETINGBOX_SINGLE_CALL_ROUTER=1).

Collapses the legacy two LLM round-trips — orchestrator router (which agent) +
per-agent planner (which tool) — into ONE native function-calling decision over
a unified, deduplicated tool catalog auto-built from the agent JSONs
(guidelines.tool_selection_rules = source of truth) plus the parameter contracts
below.

Validated against the 96-case routing suite: agent 97%->99%, tool 90%->99%,
median decision latency ~4.2s -> ~2.6s. Falls back to the legacy path on any
error, when no tool is chosen, or when the chosen tools span multiple agents.

Wiring: when enabled, process_assistant_intent calls route_and_plan() to obtain
(agent_id, steps), stashes the steps in a thread-local override, and runs the
existing single-agent dispatch unchanged — each plan_*_steps() returns the
override instead of making its own LLM call.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from agent_registry import list_agents

logger = logging.getLogger("meetingbox.single_call_router")

SINGLE_CALL_FLAG_ENV = "MEETINGBOX_SINGLE_CALL_ROUTER"


def single_call_enabled() -> bool:
  """Feature flag. Default off — preserves the legacy router+planner path."""
  return (os.getenv(SINGLE_CALL_FLAG_ENV, "0") or "").strip() == "1"


# --- General routing principles (disjoint boundaries, not per-case patches) ---
ROUTING_PRINCIPLES = (
    "You are MeetingBox's action router. Choose the SINGLE most specific tool that fulfils the "
    "user's request and fill its arguments from what the user said (you may use placeholder hints "
    "like a name or a title+date when an exact id is not given — downstream tools resolve them). "
    "ALWAYS call a tool when the request maps to one; do not ask the user for details a tool can "
    "look up itself. Call exactly one tool unless the request truly needs a short ordered sequence.\n"
    "Decision principles:\n"
    "- A reminder / to-do / 'don't forget to X' / 'note that I need to X' / 'add to my list' is a "
    "TASK tool (commitment_*) — never a memory tool. Memory tools are only for durable FACTS/"
    "preferences about the user ('remember I prefer aisle seats').\n"
    "- 'finish / complete / mark done / cross off the <X> task (or reminder)' → commitment_upsert with "
    "the task title as a hint and status='completed'. This is always a task action, even if the task's "
    "name mentions calendar, email, or another topic.\n"
    "- Editing recipients/subject/body of a draft you don't have an id for → pass draft_id='current'.\n"
    "- Reading, summarizing, replying to, forwarding, or drafting EMAIL is a gmail_* tool — never memory. "
    "Composing a NEW email the user wants sent now → gmail_send_email. Saving a new draft → gmail_create_draft.\n"
    "- EMAIL ID RESOLUTION ($PREV contract): gmail action tools that need an existing id "
    "(gmail_update_draft, gmail_send_draft, gmail_add_recipients, gmail_remove_recipients need draft_id; "
    "gmail_reply_to_thread/gmail_reply_all need thread_id; gmail_forward_email/gmail_archive_email/"
    "gmail_delete_email/gmail_read_email need message_id). When you do NOT already have that id, emit TWO "
    "tool calls IN THE SAME RESPONSE: first gmail_list_recent with a precise q "
    "(e.g. q='in:drafts', q='from:Trilok subject:Lunch', q='subject:Catchup'), then the action tool with "
    "the id argument set to the literal string \"$PREV\" — the system substitutes the real id from the "
    "list result at execution time. Editing an existing draft (add/remove recipients, change subject/body) "
    "uses gmail_update_draft / gmail_add_recipients / gmail_remove_recipients with draft_id=\"$PREV\" after a "
    "gmail_list_recent q='in:drafts'. Never pass a literal 'current' or a subject string as an id.\n"
    "- 'Compare X and Y', 'research', 'deep dive', 'investigate' → research_deep_research; other current "
    "facts use the specific research tool; general knowledge you already know needs NO tool.\n"
    "- CALENDAR — never call calendar_list_upcoming before a write. When the user NAMES an event "
    "('the all-hands on Friday', 'my 3 PM', 'the Friday review', 'the weekly standup'), call the write "
    "tool DIRECTLY with a title+date hint: create→calendar_create_event, move/rename/reschedule/"
    "add-or-remove attendee→calendar_update_event, delete/cancel→calendar_delete_event, "
    "accept/decline/RSVP→calendar_rsvp_event. The write tools self-locate the event; do NOT list first.\n"
    "- 'Am I free / availability / find a slot / when am I free / open time' → calendar_suggest_free_slots "
    "(NOT calendar_list_upcoming). Use calendar_list_upcoming ONLY when the user explicitly asks to "
    "see/show/list their schedule.\n"
    "- Only emit a clarify-style response when a genuinely required field cannot be hinted at all."
)


def _obj(props: dict, required: list[str] | None = None) -> dict:
  s: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": True}
  if required:
    s["required"] = required
  return s


def _S(d: str = "") -> dict:
  return {"type": "string", "description": d}


def _ARR(d: str = "") -> dict:
  return {"type": "array", "items": {"type": "string"}, "description": d}


def _INT(d: str = "") -> dict:
  return {"type": "integer", "description": d}


# Parameter contracts for the high-traffic tools. A tool the model can fill
# arguments for is a tool the model will confidently call — empty schemas cause
# the model to fall back to asking. Field names mirror the agent tool_selection_rules
# so the args drop straight into the existing dispatch/execution code unchanged.
PARAM_SCHEMAS: dict[str, dict] = {
    "gmail_send_email": _obj({"to": _S("recipient email or name hint"), "subject": _S(), "body": _S(),
                              "cc": _ARR(), "bcc": _ARR()}, ["to"]),
    "gmail_create_draft": _obj({"to": _S(), "subject": _S(), "body": _S(), "cc": _ARR(), "bcc": _ARR()}),
    "gmail_update_draft": _obj({"draft_id": _S("draft id; use 'current' for the draft in progress"),
                                "subject": _S(), "body": _S(), "to": _S()}),
    "gmail_add_recipients": _obj({"draft_id": _S("draft id; use 'current' for the draft in progress"),
                                  "to_add": _ARR("emails or name hints"), "cc_add": _ARR(), "bcc_add": _ARR()}),
    "gmail_remove_recipients": _obj({"draft_id": _S("draft id; use 'current' for the draft in progress"),
                                     "to_remove": _ARR("emails or name hints"), "cc_remove": _ARR(),
                                     "bcc_remove": _ARR()}),
    "gmail_reply_to_thread": _obj({"thread_id": _S("thread hint"), "body": _S()}, ["body"]),
    "gmail_reply_all": _obj({"thread_id": _S("thread hint"), "body": _S()}, ["body"]),
    "gmail_forward_email": _obj({"message_id": _S("message hint"), "to": _S(), "body": _S()}, ["to"]),
    "gmail_archive_email": _obj({"message_id": _S("message hint")}),
    "gmail_delete_email": _obj({"message_id": _S("message hint")}),
    "gmail_read_email": _obj({"message_id": _S("message hint or sender/subject")}),
    "gmail_list_recent": _obj({"q": _S("gmail search string, e.g. in:drafts"), "max_results": _INT()}),
    "calendar_create_event": _obj({"title": _S(), "start_time": _S("ISO local wall time"),
                                   "duration_minutes": _INT(), "attendees": _ARR(),
                                   "recurrence": _S("RRULE for recurring"),
                                   "add_meet_link": {"type": "boolean"}}, ["title"]),
    "calendar_update_event": _obj({"title": _S("event name hint"), "date": _S("YYYY-MM-DD"),
                                   "new_start_time": _S(), "new_date": _S(), "new_duration_minutes": _INT(),
                                   "new_title": _S(), "attendees_add": _ARR(), "attendees_remove": _ARR(),
                                   "new_recurrence": _S()}),
    "calendar_delete_event": _obj({"title": _S("event name only, not a sentence"), "date": _S("YYYY-MM-DD")}),
    "calendar_rsvp_event": _obj({"title": _S("event name hint"), "date": _S("YYYY-MM-DD"),
                                 "response_status": {"type": "string",
                                                     "enum": ["accepted", "declined", "tentative"]}},
                                ["response_status"]),
    "calendar_suggest_free_slots": _obj({"days_ahead": _INT(), "duration_minutes": _INT(),
                                         "work_start_hhmm": _S(), "work_end_hhmm": _S()}),
    "calendar_list_upcoming": _obj({"max_results": _INT(), "date": _S("YYYY-MM-DD"), "days_ahead": _INT()}),
    "commitment_upsert": _obj({"id": _S("present only when updating an existing task"), "title": _S(),
                               "detail": _S(), "due_at": _S("ISO"), "status": _S()}),
    "commitment_list": _obj({"status": _S("active|completed|snoozed|cancelled|all"), "max_results": _INT()}),
    "extract_tasks_from_emails": _obj({"message_ids": _ARR()}),
    "research_web_search": _obj({"query": _S(), "num_results": _INT()}, ["query"]),
    "research_news": _obj({"category": _S(), "topic": _S()}),
    "research_weather": _obj({"city": _S()}),
    "research_currency_convert": _obj({"amount": {"type": "number"}, "from": _S(), "to": _S()}),
    "research_stock_price": _obj({"ticker": _S()}, ["ticker"]),
    "research_sports_score": _obj({"query": _S()}, ["query"]),
    "research_deep_research": _obj({"topic": _S()}, ["topic"]),
    "memory_search_meetings": _obj({"query": _S(), "participant": _S(), "date_from": _S(), "date_to": _S()}),
    "memory_fetch_meeting": _obj({"meeting_id": _S()}, ["meeting_id"]),
}


# --- Unified catalog (cached) -------------------------------------------------
_catalog_lock = threading.Lock()
_catalog: dict[str, Any] | None = None


def _rule_for(tool: str, rules: list[str]) -> str:
  for r in rules:
    rs = r.strip()
    if rs.startswith(tool) or rs.startswith(f'"{tool}"') or rs.split("—")[0].strip() == tool:
      return rs
  return ""


def _build_catalog() -> dict[str, Any]:
  """Return {tools: [anthropic tool defs], owner: {tool->agent_id}, is_write: {tool->bool}}."""
  owner: dict[str, str] = {}
  descs: dict[str, str] = {}
  is_write: dict[str, bool] = {}
  for a in list_agents():
    if a.get("system_only"):
      continue
    aid = a["id"]
    rules = (a.get("guidelines") or {}).get("tool_selection_rules", []) or []
    policies = a.get("tool_policies") or {}
    for t in a.get("tools", []):
      if t in owner:
        continue
      owner[t] = aid
      descs[t] = (_rule_for(t, rules) or f"{t}: ({aid} tool)")[:1024]
      pol = policies.get(t) if isinstance(policies.get(t), dict) else {}
      cat = (pol.get("category") or "").lower()
      is_write[t] = bool(pol.get("requires_approval")) or cat in ("outbound", "destructive")
  tools = [
      {"name": t, "description": descs[t],
       "input_schema": PARAM_SCHEMAS.get(t, {"type": "object", "properties": {}, "additionalProperties": True})}
      for t in owner
  ]
  return {"tools": tools, "owner": owner, "is_write": is_write}


def _get_catalog() -> dict[str, Any]:
  global _catalog
  if _catalog is None:
    with _catalog_lock:
      if _catalog is None:
        _catalog = _build_catalog()
        logger.info("single-call catalog built: %d tools", len(_catalog["owner"]))
  return _catalog


# --- Anthropic client (cached) ------------------------------------------------
_client = None
_client_lock = threading.Lock()


def _get_client():
  global _client
  if _client is not None:
    return _client
  key = os.getenv("ANTHROPIC_API_KEY", "").strip()
  if not key:
    return None
  with _client_lock:
    if _client is None:
      from anthropic import Anthropic

      _client = Anthropic(api_key=key)
  return _client


# --- Per-agent step override (thread-local) -----------------------------------
_override = threading.local()


def set_override(agent_id: str, steps: list[dict[str, Any]]) -> None:
  store = getattr(_override, "steps", None)
  if store is None:
    store = {}
    _override.steps = store
  store[agent_id] = steps


def pop_override(agent_id: str) -> list[dict[str, Any]] | None:
  store = getattr(_override, "steps", None)
  if not store:
    return None
  return store.pop(agent_id, None)


def clear_overrides() -> None:
  _override.steps = {}


# --- The single call ----------------------------------------------------------
def route_and_plan(message: str) -> tuple[str, list[dict[str, Any]], str] | None:
  """
  One native function-calling decision. Returns (agent_id, steps, rationale) or
  None to signal the caller should fall back to the legacy router+planner path.

  steps: [{"tool": str, "args": dict, "is_write": bool}] for the single owning
  agent. Returns None when no tool is chosen or when chosen tools span multiple
  agents (true multi-agent — left to the legacy/multi-agent path).
  """
  text = (message or "").strip()
  if not text:
    return None
  client = _get_client()
  if client is None:
    return None
  cat = _get_catalog()
  model = (os.getenv("AI_MODEL") or "claude-sonnet-4-5-20250929").strip()
  try:
    t0 = time.perf_counter()
    resp = client.messages.create(
        model=model,
        max_tokens=900,
        system=ROUTING_PRINCIPLES,
        tools=cat["tools"],
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": text}],
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
  except Exception:
    logger.exception("single-call route_and_plan LLM failed")
    return None

  owner = cat["owner"]
  is_write = cat["is_write"]
  steps: list[dict[str, Any]] = []
  agents_seen: list[str] = []
  for block in resp.content:
    if getattr(block, "type", "") != "tool_use":
      continue
    name = getattr(block, "name", "") or ""
    if name not in owner:
      continue
    aid = owner[name]
    if aid not in agents_seen:
      agents_seen.append(aid)
    raw_args = getattr(block, "input", None)
    args = dict(raw_args) if isinstance(raw_args, dict) else {}
    steps.append({"tool": name, "args": args, "is_write": is_write.get(name, False)})

  if not steps:
    logger.info("single-call: no tool chosen (%dms); falling back", elapsed_ms)
    return None
  if len(agents_seen) > 1:
    logger.info("single-call: multi-agent tools %s; deferring to legacy path", agents_seen)
    return None

  agent_id = agents_seen[0]
  own_steps = [s for s in steps if owner.get(s["tool"]) == agent_id]
  logger.info("single-call route=%s tools=%s (%dms)", agent_id,
              [s["tool"] for s in own_steps], elapsed_ms)
  return agent_id, own_steps, f"single_call:{elapsed_ms}ms"
