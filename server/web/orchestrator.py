<<<<<<< Updated upstream
"""
Route free-form user text to a specialist agent id (Phase 1 orchestrator).

Routing is intent-first and LLM-driven — there is no keyword-score decision in the
normal path:

1) LLM intent classification (primary): OpenAI (when OPENAI_API_KEY is set) with
   optional Mem0 context + agent descriptions; otherwise Anthropic (AI_MODEL). The
   model reads the agent capability descriptions and the user's message and infers
   which specialist the requested ACTION belongs to. When a provider runs, its
   decision (including an explicit "none") is authoritative.
2) Keyword triggers (offline safety net only): used solely when NO LLM provider is
   configured or every provider errors out (e.g. no API key, network failure). This
   keeps the device functional offline; it never overrides a working LLM decision.

Phase 2 (opt-in, gated by env MEETINGBOX_MULTI_AGENT_PLANNER=1):
   plan_multi_agent_intent(): returns an ordered list of specialist steps so a single
   user turn can chain agents (e.g. memory_agent -> communication_agent). Falls back
   to single-agent routing when the planner declines or is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from agent_registry import list_agents

logger = logging.getLogger("meetingbox.orchestrator")

MULTI_AGENT_MAX_STEPS = 4
MULTI_AGENT_FLAG_ENV = "MEETINGBOX_MULTI_AGENT_PLANNER"


def _is_system_only(agent: dict[str, Any]) -> bool:
  """An agent is hidden from the user-facing router when system_only=true in its JSON."""
  return bool(agent.get("system_only"))


@dataclass
class RouteResult:
  agent_id: str | None
  method: str
  rationale: str = ""


@dataclass
class PlanStep:
  agent_id: str
  message: str
  depends_on_prior_results: bool = False
  rationale: str = ""


@dataclass
class MultiAgentPlan:
  steps: list[PlanStep] = field(default_factory=list)
  method: str = "none"
  rationale: str = ""


def multi_agent_enabled() -> bool:
  """Feature flag for the multi-step planner. Default off — keeps legacy single-agent path."""
  return (os.getenv(MULTI_AGENT_FLAG_ENV, "0") or "").strip() == "1"


_anthropic_client = None


def _get_anthropic():
  global _anthropic_client
  if _anthropic_client is None:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
      return None
    from anthropic import Anthropic

    _anthropic_client = Anthropic(api_key=key)
  return _anthropic_client


# Opt-D: trivial messages that carry no routing signal — skip Mem0 lookup.
_TRIVIAL_PATTERNS = frozenset({
    "ok", "okay", "yes", "no", "thanks", "thank you", "sure", "got it",
    "sounds good", "great", "cool", "nice", "perfect", "alright", "fine",
    "nope", "nah", "yep", "yup", "right", "correct", "exactly", "agreed",
})


def _is_trivial_message(message: str) -> bool:
    """Return True for short, context-free ack messages that need no Mem0 routing context."""
    stripped = (message or "").strip().lower().rstrip("!.,?")
    return stripped in _TRIVIAL_PATTERNS or (len(stripped) <= 3 and stripped.isalpha())


def _mem0_router_snippet(user_id: str | None, message: str) -> str:
  """Short Mem0 recall for routing only (same store as assistant augment).

  Opt-D: skipped entirely for trivial one/two-word acknowledgements.
  """
  if not (user_id or "").strip():
    return ""
  if _is_trivial_message(message):
    logger.debug("mem0 router snippet skipped (trivial message) user=%s", user_id)
    return ""
  try:
    from services.mem0_service import search_context_for_prompt

    blob = search_context_for_prompt(str(user_id).strip(), message, top_k=6)
  except Exception:
    logger.debug("Mem0 router context skipped", exc_info=True)
    return ""
  if not blob:
    return ""
  return (
    "\nLong-term memory snippets for this user (reference only; may be incomplete):\n"
    f"<<<MEM0\n{blob[:6000]}\nMEM0>>>\n"
  )


def _router_candidates() -> tuple[set[str], list[dict[str, Any]]]:
  valid_ids: set[str] = set()
  candidates: list[dict[str, Any]] = []
  for agent in list_agents():
    if _is_system_only(agent):
      continue
    valid_ids.add(agent["id"])
    candidates.append(
        {
          "id": agent["id"],
          "name": agent.get("name"),
          "description": (agent.get("description") or "")[:400],
        }
    )
  return valid_ids, candidates


def _score_triggers(message_lower: str, triggers: list[str]) -> int:
  if not triggers:
    return 0
  score = 0
  for t in triggers:
    needle = (t or "").strip().lower()
    if len(needle) < 2:
      continue
    if needle in message_lower:
      score += 1 + min(len(needle), 20) // 10
  return score


def _strip_email_addresses(text: str) -> str:
  """Remove email-like tokens so embedded addresses don't inflate trigger scores.

  E.g. 'add vivek@gmail.com to the meeting' would otherwise match 'gmail' and
  'mail' triggers for the communication_agent even though this is a calendar op.
  """
  return re.sub(r"\S+@\S+\.\S+", " ", text)


def route_with_triggers(message: str) -> RouteResult | None:
  msg_lower = _strip_email_addresses(message.lower().strip())
  if not msg_lower.strip():
    return None

  ranked: list[tuple[str, int, int]] = []
  for agent in list_agents():
    aid = agent["id"]
    if _is_system_only(agent):
      continue
    triggers = agent.get("triggers") or []
    if not isinstance(triggers, list):
      triggers = []
    s = _score_triggers(msg_lower, triggers)
    if s > 0:
      ranked.append((aid, s, int(agent.get("priority") or 0)))

  if not ranked:
    return None
  ranked.sort(key=lambda t: (-t[1], -t[2], t[0]))
  win_id, win_score, _ = ranked[0]
  return RouteResult(agent_id=win_id, method="triggers", rationale=f"trigger_score={win_score}")


def _parse_classifier_json(text: str) -> dict[str, Any]:
  text = text.strip()
  if "```json" in text:
    start = text.find("```json") + len("```json")
    end = text.find("```", start)
    text = text[start:end].strip()
  elif "```" in text:
    start = text.find("```") + 3
    end = text.find("```", start)
    text = text[start:end].strip()
  return json.loads(text)


# Routing is decided on the ACTION the user wants performed, not on topical words
# that happen to appear in the message. This guidance is provider-agnostic and is
# shared by the OpenAI and Anthropic routers.
_ROUTER_INSTRUCTIONS = (
  "You are an intent router. Read the user's message and decide which ONE specialist "
  "agent should perform it, or none.\n"
  "Return a JSON object with keys: \"agent_id\" (one of the listed candidate ids, or "
  "\"none\") and \"rationale\" (one short sentence).\n"
  "\n"
  "HOW TO DECIDE:\n"
  "- Route on the ACTION the user wants performed, never on topical keywords. The "
  "subject line of an email, the title of a task, or the name of a meeting is NOT a "
  "routing signal — only the operation the user is asking for matters.\n"
  "- Match that action to the candidate whose described capabilities cover it. Use the "
  "candidate descriptions, the user message, and any memory context to infer intent.\n"
  "- Prefer a specialist whenever the user wants to do something in its domain, even if "
  "the phrasing is vague or indirect. Be decisive — when a request plausibly belongs to "
  "one specialist, pick it rather than returning \"none\".\n"
  "- Any request for real-world, current, or look-up-able information — weather, news, "
  "stock or crypto prices, currency conversion, sports scores, or general facts to "
  "search the web for — belongs to the real-time web research specialist.\n"
  "- When the user refers to \"the <X> task\" or \"<X> task\" as a thing to complete, "
  "finish, update, snooze, or list, <X> is the NAME of an existing to-do item — the "
  "action belongs to the tasks specialist, regardless of what <X> is (e.g. 'complete "
  "the email task' / 'finish the report task' = completing a to-do item).\n"
  "- 'Remind me to X' / 'don't forget to X' / 'note to self X' always route to the "
  "tasks specialist by default — they create a to-do item. Only route to the calendar "
  "specialist when the user explicitly says to block or freeze a slot on the calendar "
  "(e.g. 'add a calendar reminder', 'block time on my calendar to remind me', 'set a "
  "calendar alert for X').\n"
  "- Use \"none\" ONLY when the message has no plausible connection to any candidate's "
  "domain.\n"
  "\n"
  "EXAMPLES (illustrate the principle, not an exhaustive keyword list):\n"
  "- 'Send an email with the subject \"Availability tomorrow between 2 and 5 PM\"' -> the "
  "action is SENDING EMAIL (communication), even though it mentions availability. It is "
  "NOT a calendar request.\n"
  "- 'Mark the email task as done' -> the action is COMPLETING A TASK (tasks), even "
  "though it mentions email.\n"
  "- 'What did we decide in the standup?' / 'get me the transcript from the client "
  "call' / 'pull up notes from the planning session' -> the action is RECALLING A PAST "
  "MEETING (memory): transcripts, notes, summaries, and decisions from any previous "
  "meeting, call, or session all belong to the memory specialist.\n"
  "- 'Am I free Thursday afternoon?' -> the action is CHECKING THE CALENDAR (calendar).\n"
  "- 'What's the weather in Bangalore?' / 'AAPL stock price' / 'latest tech news' -> "
  "these are LOOK-UP requests for live information (research).\n"
)


def _classify_route_payload(
  data: dict[str, Any],
  valid_ids: set[str],
  provider: str,
) -> RouteResult | None:
  """Interpret a parsed router JSON payload from a provider that successfully ran.

  Returns:
    - RouteResult(agent_id=<id>)            when the model chose a valid specialist.
    - RouteResult(agent_id=None, ..._none)  when the model explicitly chose "none".
    - None                                  when the model returned an unknown id
                                            (treated as a failure so the next provider
                                            can try).
  """
  raw_id = str(data.get("agent_id") or "none").strip()
  rationale = str(data.get("rationale") or "").strip()
  if raw_id == "none" or not raw_id:
    return RouteResult(
      agent_id=None,
      method=f"{provider}_none",
      rationale=rationale or "no specialist matched",
    )
  if raw_id not in valid_ids:
    logger.warning("Router (%s) returned unknown agent_id %r", provider, raw_id)
    return None
  return RouteResult(agent_id=raw_id, method=provider, rationale=rationale)


def _route_llm_openai(
  candidates: list[dict[str, Any]],
  mem0_part: str,
  user_ask: str,
  valid_ids: set[str],
) -> RouteResult | None:
  """OpenAI router. Returns a RouteResult when the model ran (including an explicit
  'none'); returns None only when OpenAI is unavailable or the call errored so the
  caller can cascade to Anthropic."""
  oa_key = os.getenv("OPENAI_API_KEY", "").strip()
  if not oa_key:
    return None
  try:
    from openai import OpenAI

    router_model = (os.getenv("OPENAI_ROUTER_MODEL") or "gpt-4o-mini").strip()
    client = OpenAI(api_key=oa_key)
    user_payload = (
      f"Candidates (choose at most one id):\n{json.dumps(candidates, indent=2)}\n"
      f"{mem0_part}\nUser message:\n{user_ask}\n"
    )
    resp = client.chat.completions.create(
      model=router_model,
      max_tokens=250,
      temperature=0,
      response_format={"type": "json_object"},
      messages=[
        {"role": "system", "content": _ROUTER_INSTRUCTIONS},
        {"role": "user", "content": user_payload},
      ],
    )
    raw = (resp.choices[0].message.content or "").strip()
    data = json.loads(raw)
  except Exception:
    logger.exception("OpenAI intent router failed; trying Anthropic if configured")
    return None
  return _classify_route_payload(data, valid_ids, "openai")


def _route_llm_anthropic(
  candidates: list[dict[str, Any]],
  mem0_part: str,
  user_ask: str,
  valid_ids: set[str],
) -> RouteResult | None:
  """Anthropic router. Same contract as _route_llm_openai."""
  client = _get_anthropic()
  if not client:
    return None
  prompt = (
    _ROUTER_INSTRUCTIONS
    + "\nReturn **only** valid JSON: {\"agent_id\": \"<id>|none\", \"rationale\": \"one short sentence\"}\n\n"
    f"Candidates:\n{json.dumps(candidates, indent=2)}\n"
    f"{mem0_part}\nUser message:\n{user_ask}\n"
  )
  model = os.getenv("AI_MODEL", "claude-sonnet-4-5-20250929")
  try:
    resp = client.messages.create(
      model=model,
      max_tokens=200,
      temperature=0,
      messages=[{"role": "user", "content": prompt}],
    )
    block = resp.content[0]
    text = getattr(block, "text", "") or ""
    data = _parse_classifier_json(text)
  except (json.JSONDecodeError, IndexError, TypeError):
    logger.warning("Anthropic router returned non-JSON / unparseable payload")
    return None
  except Exception:
    logger.exception("Anthropic intent router failed")
    return None
  return _classify_route_payload(data, valid_ids, "anthropic")


def route_with_llm(message: str, user_id: str | None = None) -> RouteResult | None:
  """Intelligent intent router.

  Returns a RouteResult when a provider successfully classified the message — even if
  that classification is an explicit "none" (agent_id=None, method ending in "_none").
  Returns None ONLY when no LLM provider is configured/reachable, so the caller knows
  to fall back to the offline keyword safety net.
  """
  valid_ids, candidates = _router_candidates()
  if not valid_ids:
    return None

  mem0_part = _mem0_router_snippet(user_id, message)
  user_ask = (message or "").strip()[:4000]

  # OpenAI preferred (matches the TTS / Whisper stack); Anthropic as provider fallback.
  res = _route_llm_openai(candidates, mem0_part, user_ask, valid_ids)
  if res is not None:
    return res
  return _route_llm_anthropic(candidates, mem0_part, user_ask, valid_ids)


def route_intent(message: str, user_id: str | None = None) -> RouteResult:
  """Choose a specialist agent id (or none) for the user's message.

  Intent-first: the LLM router decides. Keyword triggers are consulted ONLY when no
  LLM provider could run (no API key / all providers errored), as an offline safety
  net — they never override a working LLM decision.
  """
  text = (message or "").strip()
  if not text:
    return RouteResult(agent_id=None, method="empty", rationale="empty message")

  llm_hit = route_with_llm(text, user_id=user_id)
  if llm_hit is not None:
    # A provider ran. Trust its decision, including an explicit "none".
    return llm_hit

  # No LLM provider available — degrade to keyword triggers so the device still works.
  trig = route_with_triggers(text)
  if trig:
    return RouteResult(
      agent_id=trig.agent_id,
      method="triggers_fallback",
      rationale=trig.rationale,
    )
  return RouteResult(agent_id=None, method="none", rationale="no_llm_provider_no_trigger_match")


# ----------------------------- Multi-agent planner -----------------------------
#
# Opt-in via MEETINGBOX_MULTI_AGENT_PLANNER=1. Returns an ordered plan that the
# assistant_service executor runs step-by-step, threading prior tool_results into
# the next step's message. Each step still routes through the existing per-agent
# branch in assistant_service — no specialist behaviour changes.


_MULTI_AGENT_INSTRUCTIONS = (
  "You are a multi-agent planner. Given a user request, decide whether it needs\n"
  "ONE specialist or a short ORDERED sequence of specialists. Each specialist runs\n"
  "in isolation and can only use its own tools; the only way data flows from one\n"
  "to the next is by you marking depends_on_prior_results=true on later steps,\n"
  "which gives that step access to a compact JSON of prior tool results.\n"
  "\n"
  "Return ONLY valid JSON with this exact shape:\n"
  "{\n"
  "  \"steps\": [\n"
  "    {\n"
  "      \"agent_id\": \"<one of the candidate ids>\",\n"
  "      \"message\": \"<a focused sub-task for this specialist, in the user's voice>\",\n"
  "      \"depends_on_prior_results\": true|false,\n"
  "      \"rationale\": \"<one short sentence>\"\n"
  "    }\n"
  "  ],\n"
  "  \"rationale\": \"<one short sentence on the overall plan>\"\n"
  "}\n"
  "\n"
  "PLANNING RULES:\n"
  f"- At most {MULTI_AGENT_MAX_STEPS} steps. Prefer 1 step when one specialist suffices.\n"
  "- Use multiple steps ONLY when the request genuinely needs data or actions from\n"
  "  more than one domain (e.g. read a past meeting summary AND draft an email about it).\n"
  "- The FIRST step must be a READ specialist (e.g. memory_agent, calendar_agent\n"
  "  listing) when later steps need its data; set its depends_on_prior_results=false.\n"
  "- Set depends_on_prior_results=true ONLY for steps that need the previous step's\n"
  "  data to do their job (e.g. communication_agent drafting an email about a fetched summary).\n"
  "- Rewrite each step's message as a clear standalone instruction for that specialist;\n"
  "  do not assume the specialist sees the rest of the plan.\n"
  "- For steps with depends_on_prior_results=true, write the message as if the prior\n"
  "  data is already provided — e.g. \"Draft an email to john@x.com with the meeting\n"
  "  summary from prior results.\" Do NOT inline the data yourself; the executor will.\n"
  "- If you cannot map the request to any specialist, return {\"steps\": []}.\n"
  "\n"
  "COMMON TWO-STEP PATTERNS (use these as templates):\n"
  "- 'Email everyone on the <event> meeting saying X' / 'Notify all attendees of <event> that X':\n"
  "    Step 1: calendar_agent — \"List the upcoming '<event>' meeting and surface the attendees.\" (depends_on_prior_results=false)\n"
  "    Step 2: communication_agent — \"Draft an email to the attendees from the previous step saying X.\" (depends_on_prior_results=true)\n"
  "- 'Draft an email summarising my last meeting and send to john@x.com':\n"
  "    Step 1: memory_agent — \"Pull the summary of the most recent meeting.\" (depends_on_prior_results=false)\n"
  "    Step 2: communication_agent — \"Draft an email to john@x.com with the summary from prior results.\" (depends_on_prior_results=true)\n"
)


def _safe_int(val: Any, default: int) -> int:
  try:
    return int(val)
  except (TypeError, ValueError):
    return default


def _normalize_plan_steps(
  data: dict[str, Any],
  valid_ids: set[str],
) -> list[PlanStep]:
  raw_steps = data.get("steps") if isinstance(data, dict) else None
  if not isinstance(raw_steps, list):
    return []
  out: list[PlanStep] = []
  for idx, item in enumerate(raw_steps):
    if not isinstance(item, dict):
      continue
    aid = str(item.get("agent_id") or "").strip()
    if not aid or aid not in valid_ids:
      logger.warning("multi-agent planner: dropping step %d with invalid agent_id=%r", idx, aid)
      continue
    msg = str(item.get("message") or "").strip()
    if not msg:
      continue
    depends = bool(item.get("depends_on_prior_results"))
    # First step never depends on prior results — there are none.
    if not out:
      depends = False
    rationale = str(item.get("rationale") or "").strip()
    out.append(PlanStep(
      agent_id=aid,
      message=msg[:4000],
      depends_on_prior_results=depends,
      rationale=rationale[:400],
    ))
    if len(out) >= MULTI_AGENT_MAX_STEPS:
      break
  return out


def _call_openai_multi_planner(
  message: str,
  candidates: list[dict[str, Any]],
  mem0_part: str,
) -> dict[str, Any] | None:
  oa_key = os.getenv("OPENAI_API_KEY", "").strip()
  if not oa_key:
    return None
  try:
    from openai import OpenAI

    planner_model = (
      os.getenv("OPENAI_MULTI_AGENT_PLANNER_MODEL")
      or os.getenv("OPENAI_ROUTER_MODEL")
      or "gpt-4o-mini"
    ).strip()
    client = OpenAI(api_key=oa_key)
    user_payload = (
      f"Candidates (specialists you may pick from):\n{json.dumps(candidates, indent=2)}\n"
      f"{mem0_part}\nUser message:\n{message[:4000]}\n"
    )
    resp = client.chat.completions.create(
      model=planner_model,
      max_tokens=600,
      response_format={"type": "json_object"},
      messages=[
        {"role": "system", "content": _MULTI_AGENT_INSTRUCTIONS},
        {"role": "user", "content": user_payload},
      ],
    )
    raw = (resp.choices[0].message.content or "").strip()
    return json.loads(raw)
  except Exception:
    logger.exception("OpenAI multi-agent planner failed; will try Anthropic fallback")
    return None


def _call_anthropic_multi_planner(
  message: str,
  candidates: list[dict[str, Any]],
  mem0_part: str,
) -> dict[str, Any] | None:
  client = _get_anthropic()
  if not client:
    return None
  prompt = (
    _MULTI_AGENT_INSTRUCTIONS
    + "\nReturn ONLY the JSON object — no commentary.\n\n"
    + f"Candidates:\n{json.dumps(candidates, indent=2)}\n"
    + f"{mem0_part}\nUser message:\n{message[:4000]}\n"
  )
  model = os.getenv("AI_MODEL", "claude-sonnet-4-5-20250929")
  try:
    resp = client.messages.create(
      model=model,
      max_tokens=700,
      messages=[{"role": "user", "content": prompt}],
    )
  except Exception:
    logger.exception("Anthropic multi-agent planner failed")
    return None
  block = resp.content[0]
  text = getattr(block, "text", "") or ""
  try:
    return _parse_classifier_json(text)
  except (json.JSONDecodeError, IndexError, TypeError):
    logger.warning("Anthropic multi-agent planner returned non-JSON: %s", text[:200])
    return None


def plan_multi_agent_intent(
  message: str,
  user_id: str | None = None,
) -> MultiAgentPlan | None:
  """
  Return an ordered multi-step plan for the given user message, or None if the planner
  declined / is unavailable. Caller is responsible for falling back to single-agent
  routing when this returns None or an empty plan.
  """
  text = (message or "").strip()
  if not text:
    return None

  valid_ids, candidates = _router_candidates()
  if not valid_ids:
    return None

  mem0_part = _mem0_router_snippet(user_id, text)

  data = _call_openai_multi_planner(text, candidates, mem0_part)
  method = "openai"
  if data is None:
    data = _call_anthropic_multi_planner(text, candidates, mem0_part)
    method = "anthropic"
  if not isinstance(data, dict):
    return None

  steps = _normalize_plan_steps(data, valid_ids)
  if not steps:
    return None

  rationale = str(data.get("rationale") or "").strip()[:400]
  return MultiAgentPlan(steps=steps, method=method, rationale=rationale)

=======
"""
Route free-form user text to a specialist agent id (Phase 1 orchestrator).

Routing is intent-first and LLM-driven — there is no keyword-score decision in the
normal path:

1) LLM intent classification (primary): OpenAI (when OPENAI_API_KEY is set) with
   optional Mem0 context + agent descriptions; otherwise Anthropic (AI_MODEL). The
   model reads the agent capability descriptions and the user's message and infers
   which specialist the requested ACTION belongs to. When a provider runs, its
   decision (including an explicit "none") is authoritative.
2) Keyword triggers (offline safety net only): used solely when NO LLM provider is
   configured or every provider errors out (e.g. no API key, network failure). This
   keeps the device functional offline; it never overrides a working LLM decision.

Phase 2 (opt-in, gated by env MEETINGBOX_MULTI_AGENT_PLANNER=1):
   plan_multi_agent_intent(): returns an ordered list of specialist steps so a single
   user turn can chain agents (e.g. memory_agent -> communication_agent). Falls back
   to single-agent routing when the planner declines or is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from agent_registry import list_agents

logger = logging.getLogger("meetingbox.orchestrator")

MULTI_AGENT_MAX_STEPS = 4
MULTI_AGENT_FLAG_ENV = "MEETINGBOX_MULTI_AGENT_PLANNER"


def _is_system_only(agent: dict[str, Any]) -> bool:
  """An agent is hidden from the user-facing router when system_only=true in its JSON."""
  return bool(agent.get("system_only"))


@dataclass
class RouteResult:
  agent_id: str | None
  method: str
  rationale: str = ""


@dataclass
class PlanStep:
  agent_id: str
  message: str
  depends_on_prior_results: bool = False
  rationale: str = ""


@dataclass
class MultiAgentPlan:
  steps: list[PlanStep] = field(default_factory=list)
  method: str = "none"
  rationale: str = ""


def multi_agent_enabled() -> bool:
  """Feature flag for the multi-step planner. Default off — keeps legacy single-agent path."""
  return (os.getenv(MULTI_AGENT_FLAG_ENV, "0") or "").strip() == "1"


_anthropic_client = None


def _get_anthropic():
  global _anthropic_client
  if _anthropic_client is None:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
      return None
    from anthropic import Anthropic

    _anthropic_client = Anthropic(api_key=key)
  return _anthropic_client


def _mem0_router_snippet(user_id: str | None, message: str) -> str:
  """Short Mem0 recall for routing only (same store as assistant augment)."""
  if not (user_id or "").strip():
    return ""
  try:
    from services.mem0_service import search_context_for_prompt

    blob = search_context_for_prompt(str(user_id).strip(), message, top_k=6)
  except Exception:
    logger.debug("Mem0 router context skipped", exc_info=True)
    return ""
  if not blob:
    return ""
  return (
    "\nLong-term memory snippets for this user (reference only; may be incomplete):\n"
    f"<<<MEM0\n{blob[:6000]}\nMEM0>>>\n"
  )


def _router_candidates() -> tuple[set[str], list[dict[str, Any]]]:
  valid_ids: set[str] = set()
  candidates: list[dict[str, Any]] = []
  for agent in list_agents():
    if _is_system_only(agent):
      continue
    valid_ids.add(agent["id"])
    candidates.append(
        {
          "id": agent["id"],
          "name": agent.get("name"),
          "description": (agent.get("description") or "")[:400],
        }
    )
  return valid_ids, candidates


def _score_triggers(message_lower: str, triggers: list[str]) -> int:
  if not triggers:
    return 0
  score = 0
  for t in triggers:
    needle = (t or "").strip().lower()
    if len(needle) < 2:
      continue
    if needle in message_lower:
      score += 1 + min(len(needle), 20) // 10
  return score


def _strip_email_addresses(text: str) -> str:
  """Remove email-like tokens so embedded addresses don't inflate trigger scores.

  E.g. 'add vivek@gmail.com to the meeting' would otherwise match 'gmail' and
  'mail' triggers for the communication_agent even though this is a calendar op.
  """
  return re.sub(r"\S+@\S+\.\S+", " ", text)


def route_with_triggers(message: str) -> RouteResult | None:
  msg_lower = _strip_email_addresses(message.lower().strip())
  if not msg_lower.strip():
    return None

  ranked: list[tuple[str, int, int]] = []
  for agent in list_agents():
    aid = agent["id"]
    if _is_system_only(agent):
      continue
    triggers = agent.get("triggers") or []
    if not isinstance(triggers, list):
      triggers = []
    s = _score_triggers(msg_lower, triggers)
    if s > 0:
      ranked.append((aid, s, int(agent.get("priority") or 0)))

  if not ranked:
    return None
  ranked.sort(key=lambda t: (-t[1], -t[2], t[0]))
  win_id, win_score, _ = ranked[0]
  return RouteResult(agent_id=win_id, method="triggers", rationale=f"trigger_score={win_score}")


def _parse_classifier_json(text: str) -> dict[str, Any]:
  text = text.strip()
  if "```json" in text:
    start = text.find("```json") + len("```json")
    end = text.find("```", start)
    text = text[start:end].strip()
  elif "```" in text:
    start = text.find("```") + 3
    end = text.find("```", start)
    text = text[start:end].strip()
  return json.loads(text)


# Routing is decided on the ACTION the user wants performed, not on topical words
# that happen to appear in the message. This guidance is provider-agnostic and is
# shared by the OpenAI and Anthropic routers.
_ROUTER_INSTRUCTIONS = (
  "You are an intent router. Read the user's message and decide which ONE specialist "
  "agent should perform it, or none.\n"
  "Return a JSON object with keys: \"agent_id\" (one of the listed candidate ids, or "
  "\"none\") and \"rationale\" (one short sentence).\n"
  "\n"
  "HOW TO DECIDE:\n"
  "- Route on the ACTION the user wants performed, never on topical keywords. The "
  "subject line of an email, the title of a task, or the name of a meeting is NOT a "
  "routing signal — only the operation the user is asking for matters.\n"
  "- Match that action to the candidate whose described capabilities cover it. Use the "
  "candidate descriptions, the user message, and any memory context to infer intent.\n"
  "- Prefer a specialist whenever the user wants to do something in its domain, even if "
  "the phrasing is vague or indirect. Be decisive — when a request plausibly belongs to "
  "one specialist, pick it rather than returning \"none\".\n"
  "- Any request for real-world, current, or look-up-able information — weather, news, "
  "stock or crypto prices, currency conversion, sports scores, or general facts to "
  "search the web for — belongs to the real-time web research specialist.\n"
  "- When the user refers to \"the <X> task\" or \"<X> task\" as a thing to complete, "
  "finish, update, snooze, or list, <X> is the NAME of an existing to-do item — the "
  "action belongs to the tasks specialist, regardless of what <X> is (e.g. 'complete "
  "the email task' / 'finish the report task' = completing a to-do item).\n"
  "- 'Remind me to X' / 'don't forget to X' / 'note to self X' always route to the "
  "tasks specialist by default — they create a to-do item. Only route to the calendar "
  "specialist when the user explicitly says to block or freeze a slot on the calendar "
  "(e.g. 'add a calendar reminder', 'block time on my calendar to remind me', 'set a "
  "calendar alert for X').\n"
  "- Use \"none\" ONLY when the message has no plausible connection to any candidate's "
  "domain.\n"
  "\n"
  "EXAMPLES (illustrate the principle, not an exhaustive keyword list):\n"
  "- 'Send an email with the subject \"Availability tomorrow between 2 and 5 PM\"' -> the "
  "action is SENDING EMAIL (communication), even though it mentions availability. It is "
  "NOT a calendar request.\n"
  "- 'Mark the email task as done' -> the action is COMPLETING A TASK (tasks), even "
  "though it mentions email.\n"
  "- 'What did we decide in the standup?' / 'get me the transcript from the client "
  "call' / 'pull up notes from the planning session' -> the action is RECALLING A PAST "
  "MEETING (memory): transcripts, notes, summaries, and decisions from any previous "
  "meeting, call, or session all belong to the memory specialist.\n"
  "- 'Am I free Thursday afternoon?' -> the action is CHECKING THE CALENDAR (calendar).\n"
  "- 'What's the weather in Bangalore?' / 'AAPL stock price' / 'latest tech news' -> "
  "these are LOOK-UP requests for live information (research).\n"
)


def _classify_route_payload(
  data: dict[str, Any],
  valid_ids: set[str],
  provider: str,
) -> RouteResult | None:
  """Interpret a parsed router JSON payload from a provider that successfully ran.

  Returns:
    - RouteResult(agent_id=<id>)            when the model chose a valid specialist.
    - RouteResult(agent_id=None, ..._none)  when the model explicitly chose "none".
    - None                                  when the model returned an unknown id
                                            (treated as a failure so the next provider
                                            can try).
  """
  raw_id = str(data.get("agent_id") or "none").strip()
  rationale = str(data.get("rationale") or "").strip()
  if raw_id == "none" or not raw_id:
    return RouteResult(
      agent_id=None,
      method=f"{provider}_none",
      rationale=rationale or "no specialist matched",
    )
  if raw_id not in valid_ids:
    logger.warning("Router (%s) returned unknown agent_id %r", provider, raw_id)
    return None
  return RouteResult(agent_id=raw_id, method=provider, rationale=rationale)


def _route_llm_openai(
  candidates: list[dict[str, Any]],
  mem0_part: str,
  user_ask: str,
  valid_ids: set[str],
) -> RouteResult | None:
  """OpenAI router. Returns a RouteResult when the model ran (including an explicit
  'none'); returns None only when OpenAI is unavailable or the call errored so the
  caller can cascade to Anthropic."""
  oa_key = os.getenv("OPENAI_API_KEY", "").strip()
  if not oa_key:
    return None
  try:
    from openai import OpenAI

    router_model = (os.getenv("OPENAI_ROUTER_MODEL") or "gpt-4o-mini").strip()
    client = OpenAI(api_key=oa_key)
    user_payload = (
      f"Candidates (choose at most one id):\n{json.dumps(candidates, indent=2)}\n"
      f"{mem0_part}\nUser message:\n{user_ask}\n"
    )
    resp = client.chat.completions.create(
      model=router_model,
      max_tokens=250,
      temperature=0,
      response_format={"type": "json_object"},
      messages=[
        {"role": "system", "content": _ROUTER_INSTRUCTIONS},
        {"role": "user", "content": user_payload},
      ],
    )
    raw = (resp.choices[0].message.content or "").strip()
    data = json.loads(raw)
  except Exception:
    logger.exception("OpenAI intent router failed; trying Anthropic if configured")
    return None
  return _classify_route_payload(data, valid_ids, "openai")


def _route_llm_anthropic(
  candidates: list[dict[str, Any]],
  mem0_part: str,
  user_ask: str,
  valid_ids: set[str],
) -> RouteResult | None:
  """Anthropic router. Same contract as _route_llm_openai."""
  client = _get_anthropic()
  if not client:
    return None
  prompt = (
    _ROUTER_INSTRUCTIONS
    + "\nReturn **only** valid JSON: {\"agent_id\": \"<id>|none\", \"rationale\": \"one short sentence\"}\n\n"
    f"Candidates:\n{json.dumps(candidates, indent=2)}\n"
    f"{mem0_part}\nUser message:\n{user_ask}\n"
  )
  model = os.getenv("AI_MODEL", "claude-sonnet-4-5-20250929")
  try:
    resp = client.messages.create(
      model=model,
      max_tokens=200,
      temperature=0,
      messages=[{"role": "user", "content": prompt}],
    )
    block = resp.content[0]
    text = getattr(block, "text", "") or ""
    data = _parse_classifier_json(text)
  except (json.JSONDecodeError, IndexError, TypeError):
    logger.warning("Anthropic router returned non-JSON / unparseable payload")
    return None
  except Exception:
    logger.exception("Anthropic intent router failed")
    return None
  return _classify_route_payload(data, valid_ids, "anthropic")


def route_with_llm(message: str, user_id: str | None = None) -> RouteResult | None:
  """Intelligent intent router.

  Returns a RouteResult when a provider successfully classified the message — even if
  that classification is an explicit "none" (agent_id=None, method ending in "_none").
  Returns None ONLY when no LLM provider is configured/reachable, so the caller knows
  to fall back to the offline keyword safety net.
  """
  valid_ids, candidates = _router_candidates()
  if not valid_ids:
    return None

  mem0_part = _mem0_router_snippet(user_id, message)
  user_ask = (message or "").strip()[:4000]

  # OpenAI preferred (matches the TTS / Whisper stack); Anthropic as provider fallback.
  res = _route_llm_openai(candidates, mem0_part, user_ask, valid_ids)
  if res is not None:
    return res
  return _route_llm_anthropic(candidates, mem0_part, user_ask, valid_ids)


def route_intent(message: str, user_id: str | None = None) -> RouteResult:
  """Choose a specialist agent id (or none) for the user's message.

  Intent-first: the LLM router decides. Keyword triggers are consulted ONLY when no
  LLM provider could run (no API key / all providers errored), as an offline safety
  net — they never override a working LLM decision.
  """
  text = (message or "").strip()
  if not text:
    return RouteResult(agent_id=None, method="empty", rationale="empty message")

  llm_hit = route_with_llm(text, user_id=user_id)
  if llm_hit is not None:
    # A provider ran. Trust its decision, including an explicit "none".
    return llm_hit

  # No LLM provider available — degrade to keyword triggers so the device still works.
  trig = route_with_triggers(text)
  if trig:
    return RouteResult(
      agent_id=trig.agent_id,
      method="triggers_fallback",
      rationale=trig.rationale,
    )
  return RouteResult(agent_id=None, method="none", rationale="no_llm_provider_no_trigger_match")


# ----------------------------- Multi-agent planner -----------------------------
#
# Opt-in via MEETINGBOX_MULTI_AGENT_PLANNER=1. Returns an ordered plan that the
# assistant_service executor runs step-by-step, threading prior tool_results into
# the next step's message. Each step still routes through the existing per-agent
# branch in assistant_service — no specialist behaviour changes.


_MULTI_AGENT_INSTRUCTIONS = (
  "You are a multi-agent planner. Given a user request, decide whether it needs\n"
  "ONE specialist or a short ORDERED sequence of specialists. Each specialist runs\n"
  "in isolation and can only use its own tools; the only way data flows from one\n"
  "to the next is by you marking depends_on_prior_results=true on later steps,\n"
  "which gives that step access to a compact JSON of prior tool results.\n"
  "\n"
  "Return ONLY valid JSON with this exact shape:\n"
  "{\n"
  "  \"steps\": [\n"
  "    {\n"
  "      \"agent_id\": \"<one of the candidate ids>\",\n"
  "      \"message\": \"<a focused sub-task for this specialist, in the user's voice>\",\n"
  "      \"depends_on_prior_results\": true|false,\n"
  "      \"rationale\": \"<one short sentence>\"\n"
  "    }\n"
  "  ],\n"
  "  \"rationale\": \"<one short sentence on the overall plan>\"\n"
  "}\n"
  "\n"
  "PLANNING RULES:\n"
  f"- At most {MULTI_AGENT_MAX_STEPS} steps. Prefer 1 step when one specialist suffices.\n"
  "- Use multiple steps ONLY when the request genuinely needs data or actions from\n"
  "  more than one domain (e.g. read a past meeting summary AND draft an email about it).\n"
  "- The FIRST step must be a READ specialist (e.g. memory_agent, calendar_agent\n"
  "  listing) when later steps need its data; set its depends_on_prior_results=false.\n"
  "- Set depends_on_prior_results=true ONLY for steps that need the previous step's\n"
  "  data to do their job (e.g. communication_agent drafting an email about a fetched summary).\n"
  "- Rewrite each step's message as a clear standalone instruction for that specialist;\n"
  "  do not assume the specialist sees the rest of the plan.\n"
  "- For steps with depends_on_prior_results=true, write the message as if the prior\n"
  "  data is already provided — e.g. \"Draft an email to john@x.com with the meeting\n"
  "  summary from prior results.\" Do NOT inline the data yourself; the executor will.\n"
  "- If you cannot map the request to any specialist, return {\"steps\": []}.\n"
  "\n"
  "COMMON TWO-STEP PATTERNS (use these as templates):\n"
  "- 'Email everyone on the <event> meeting saying X' / 'Notify all attendees of <event> that X':\n"
  "    Step 1: calendar_agent — \"List the upcoming '<event>' meeting and surface the attendees.\" (depends_on_prior_results=false)\n"
  "    Step 2: communication_agent — \"Draft an email to the attendees from the previous step saying X.\" (depends_on_prior_results=true)\n"
  "- 'Draft an email summarising my last meeting and send to john@x.com':\n"
  "    Step 1: memory_agent — \"Pull the summary of the most recent meeting.\" (depends_on_prior_results=false)\n"
  "    Step 2: communication_agent — \"Draft an email to john@x.com with the summary from prior results.\" (depends_on_prior_results=true)\n"
)


def _safe_int(val: Any, default: int) -> int:
  try:
    return int(val)
  except (TypeError, ValueError):
    return default


def _normalize_plan_steps(
  data: dict[str, Any],
  valid_ids: set[str],
) -> list[PlanStep]:
  raw_steps = data.get("steps") if isinstance(data, dict) else None
  if not isinstance(raw_steps, list):
    return []
  out: list[PlanStep] = []
  for idx, item in enumerate(raw_steps):
    if not isinstance(item, dict):
      continue
    aid = str(item.get("agent_id") or "").strip()
    if not aid or aid not in valid_ids:
      logger.warning("multi-agent planner: dropping step %d with invalid agent_id=%r", idx, aid)
      continue
    msg = str(item.get("message") or "").strip()
    if not msg:
      continue
    depends = bool(item.get("depends_on_prior_results"))
    # First step never depends on prior results — there are none.
    if not out:
      depends = False
    rationale = str(item.get("rationale") or "").strip()
    out.append(PlanStep(
      agent_id=aid,
      message=msg[:4000],
      depends_on_prior_results=depends,
      rationale=rationale[:400],
    ))
    if len(out) >= MULTI_AGENT_MAX_STEPS:
      break
  return out


def _call_openai_multi_planner(
  message: str,
  candidates: list[dict[str, Any]],
  mem0_part: str,
) -> dict[str, Any] | None:
  oa_key = os.getenv("OPENAI_API_KEY", "").strip()
  if not oa_key:
    return None
  try:
    from openai import OpenAI

    planner_model = (
      os.getenv("OPENAI_MULTI_AGENT_PLANNER_MODEL")
      or os.getenv("OPENAI_ROUTER_MODEL")
      or "gpt-4o-mini"
    ).strip()
    client = OpenAI(api_key=oa_key)
    user_payload = (
      f"Candidates (specialists you may pick from):\n{json.dumps(candidates, indent=2)}\n"
      f"{mem0_part}\nUser message:\n{message[:4000]}\n"
    )
    resp = client.chat.completions.create(
      model=planner_model,
      max_tokens=600,
      response_format={"type": "json_object"},
      messages=[
        {"role": "system", "content": _MULTI_AGENT_INSTRUCTIONS},
        {"role": "user", "content": user_payload},
      ],
    )
    raw = (resp.choices[0].message.content or "").strip()
    return json.loads(raw)
  except Exception:
    logger.exception("OpenAI multi-agent planner failed; will try Anthropic fallback")
    return None


def _call_anthropic_multi_planner(
  message: str,
  candidates: list[dict[str, Any]],
  mem0_part: str,
) -> dict[str, Any] | None:
  client = _get_anthropic()
  if not client:
    return None
  prompt = (
    _MULTI_AGENT_INSTRUCTIONS
    + "\nReturn ONLY the JSON object — no commentary.\n\n"
    + f"Candidates:\n{json.dumps(candidates, indent=2)}\n"
    + f"{mem0_part}\nUser message:\n{message[:4000]}\n"
  )
  model = os.getenv("AI_MODEL", "claude-sonnet-4-5-20250929")
  try:
    resp = client.messages.create(
      model=model,
      max_tokens=700,
      messages=[{"role": "user", "content": prompt}],
    )
  except Exception:
    logger.exception("Anthropic multi-agent planner failed")
    return None
  block = resp.content[0]
  text = getattr(block, "text", "") or ""
  try:
    return _parse_classifier_json(text)
  except (json.JSONDecodeError, IndexError, TypeError):
    logger.warning("Anthropic multi-agent planner returned non-JSON: %s", text[:200])
    return None


def plan_multi_agent_intent(
  message: str,
  user_id: str | None = None,
) -> MultiAgentPlan | None:
  """
  Return an ordered multi-step plan for the given user message, or None if the planner
  declined / is unavailable. Caller is responsible for falling back to single-agent
  routing when this returns None or an empty plan.
  """
  text = (message or "").strip()
  if not text:
    return None

  valid_ids, candidates = _router_candidates()
  if not valid_ids:
    return None

  mem0_part = _mem0_router_snippet(user_id, text)

  data = _call_openai_multi_planner(text, candidates, mem0_part)
  method = "openai"
  if data is None:
    data = _call_anthropic_multi_planner(text, candidates, mem0_part)
    method = "anthropic"
  if not isinstance(data, dict):
    return None

  steps = _normalize_plan_steps(data, valid_ids)
  if not steps:
    return None

  rationale = str(data.get("rationale") or "").strip()[:400]
  return MultiAgentPlan(steps=steps, method=method, rationale=rationale)

>>>>>>> Stashed changes
