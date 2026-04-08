"""
Route free-form user text to a specialist agent id (Phase 1 orchestrator).

Uses trigger overlap from agent JSON first, then optional Anthropic classification.
The meeting_agent is excluded from interactive routing (system pipeline only).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from agent_registry import list_agents

logger = logging.getLogger("meetingbox.orchestrator")

SYSTEM_ONLY_AGENTS = frozenset({"meeting_agent"})


@dataclass
class RouteResult:
  agent_id: str | None
  method: str
  rationale: str = ""


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
    if aid in SYSTEM_ONLY_AGENTS:
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


def route_with_llm(message: str) -> RouteResult | None:
  client = _get_anthropic()
  if not client:
    return None

  candidates: list[dict[str, Any]] = []
  valid_ids: set[str] = set()
  for agent in list_agents():
    if agent["id"] in SYSTEM_ONLY_AGENTS:
      continue
    valid_ids.add(agent["id"])
    candidates.append(
      {
        "id": agent["id"],
        "name": agent.get("name"),
        "description": (agent.get("description") or "")[:400],
      }
    )
  if not valid_ids:
    return None

  prompt = (
    "You route user messages to at most one specialist agent.\n"
    "Return **only** valid JSON: {\"agent_id\": \"<id>|none\", \"rationale\": \"one short sentence\"}\n"
    "Pick \"none\" if no specialist fits.\n\n"
    f"Candidates:\n{json.dumps(candidates, indent=2)}\n\n"
    f"User message:\n{message.strip()[:4000]}\n"
  )
  model = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")
  try:
    resp = client.messages.create(
      model=model,
      max_tokens=200,
      messages=[{"role": "user", "content": prompt}],
    )
  except Exception:
    logger.exception("Orchestrator LLM classification failed")
    return None

  block = resp.content[0]
  text = getattr(block, "text", "") or ""
  try:
    data = _parse_classifier_json(text)
  except (json.JSONDecodeError, IndexError, TypeError):
    logger.warning("Orchestrator LLM returned non-JSON: %s", text[:200])
    return None

  raw_id = str(data.get("agent_id") or "none").strip()
  if raw_id == "none" or not raw_id:
    return RouteResult(agent_id=None, method="llm", rationale=str(data.get("rationale") or ""))
  if raw_id not in valid_ids:
    return RouteResult(agent_id=None, method="llm", rationale="model returned unknown agent_id")
  return RouteResult(agent_id=raw_id, method="llm", rationale=str(data.get("rationale") or ""))


def route_intent(message: str) -> RouteResult:
  """Choose specialist agent id or none."""
  text = (message or "").strip()
  if not text:
    return RouteResult(agent_id=None, method="empty", rationale="empty message")

  hit = route_with_triggers(text)
  if hit:
    return hit

  llm_hit = route_with_llm(text)
  if llm_hit:
    return llm_hit

  return RouteResult(agent_id=None, method="none", rationale="no_trigger_no_llm_match")
