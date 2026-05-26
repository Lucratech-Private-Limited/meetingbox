"""
Route free-form user text to a specialist agent id (Phase 1 orchestrator).

1) Keyword overlap in agent JSON (fast path).
2) LLM classification: OpenAI (when OPENAI_API_KEY is set) with optional Mem0 context + agent
   descriptions; otherwise Anthropic (AI_MODEL). No hard-coded phrase routing.

Phase 2 (opt-in, gated by env MEETINGBOX_MULTI_AGENT_PLANNER=1):
   plan_multi_agent_intent(): returns an ordered list of specialist steps so a single
   user turn can chain agents (e.g. memory_agent -> communication_agent). Falls back
   to single-agent routing when the planner declines or is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
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


def route_with_triggers(message: str) -> RouteResult | None:
  msg_lower = message.lower().strip()
  if not msg_lower:
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


def _json_route_to_result(data: dict[str, Any], valid_ids: set[str]) -> RouteResult | None:
  raw_id = str(data.get("agent_id") or "none").strip()
  if raw_id == "none" or not raw_id:
    return None
  if raw_id not in valid_ids:
    logger.warning("Router returned unknown agent_id %r", raw_id)
    return None
  return RouteResult(agent_id=raw_id, method="llm", rationale=str(data.get("rationale") or ""))


def route_with_llm(message: str, user_id: str | None = None) -> RouteResult | None:
  valid_ids, candidates = _router_candidates()
  if not valid_ids:
    return None

  mem0_part = _mem0_router_snippet(user_id, message)
  user_ask = (message or "").strip()[:4000]

  instructions = (
    "You route the user message to exactly one specialist agent, or none.\n"
    "Return a JSON object with keys: \"agent_id\" (string, one of the listed ids or \"none\"), "
    "\"rationale\" (one short sentence).\n"
    "Use the candidate descriptions, the user message, and any memory context to infer intent.\n"
    "Prefer a specialist whenever the user is asking about schedules, calendar, tasks, email, "
    " Gmail, past meetings, transcripts, or device controls — even if phrasing is vague.\n"
    "Use \"none\" only if the message has no plausible connection to any candidate's domain.\n"
  )

  # --- OpenAI (preferred when key present; matches TTS / Whisper stack) ---
  oa_key = os.getenv("OPENAI_API_KEY", "").strip()
  if oa_key:
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
        response_format={"type": "json_object"},
        messages=[
          {"role": "system", "content": instructions},
          {"role": "user", "content": user_payload},
        ],
      )
      raw = (resp.choices[0].message.content or "").strip()
      data = json.loads(raw)
      hit = _json_route_to_result(data, valid_ids)
      if hit:
        hit = RouteResult(
          agent_id=hit.agent_id,
          method="openai",
          rationale=hit.rationale,
        )
        return hit
    except Exception:
      logger.exception("OpenAI intent router failed; trying Anthropic if configured")

  # --- Anthropic fallback ---
  client = _get_anthropic()
  if not client:
    return None

  prompt = (
    instructions
    + "\nReturn **only** valid JSON: {\"agent_id\": \"<id>|none\", \"rationale\": \"one short sentence\"}\n\n"
    f"Candidates:\n{json.dumps(candidates, indent=2)}\n"
    f"{mem0_part}\nUser message:\n{user_ask}\n"
  )
  model = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")
  try:
    resp = client.messages.create(
      model=model,
      max_tokens=200,
      messages=[{"role": "user", "content": prompt}],
    )
  except Exception:
    logger.exception("Anthropic intent router failed")
    return None

  block = resp.content[0]
  text = getattr(block, "text", "") or ""
  try:
    data = _parse_classifier_json(text)
  except (json.JSONDecodeError, IndexError, TypeError):
    logger.warning("Anthropic router returned non-JSON: %s", text[:200])
    return None

  hit = _json_route_to_result(data, valid_ids)
  if hit:
    return RouteResult(agent_id=hit.agent_id, method="anthropic", rationale=hit.rationale)
  return None


def route_intent(message: str, user_id: str | None = None) -> RouteResult:
  """Choose specialist agent id or none."""
  text = (message or "").strip()
  if not text:
    return RouteResult(agent_id=None, method="empty", rationale="empty message")

  hit = route_with_triggers(text)
  if hit:
    return hit

  llm_hit = route_with_llm(text, user_id=user_id)
  if llm_hit:
    return llm_hit

  return RouteResult(agent_id=None, method="none", rationale="no_trigger_no_llm_match")


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
  model = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")
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

