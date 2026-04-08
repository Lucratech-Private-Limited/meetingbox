"""
Load and validate agent descriptor JSON files under server/web/agents/.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("meetingbox.agent_registry")

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

_AGENTS_BY_ID: dict[str, dict[str, Any]] | None = None


def _agents_dir() -> Path:
  return Path(__file__).resolve().parent / "agents"


def _validate_entry(entry: dict[str, Any], source: str, stem: str) -> None:
  required = (
    "id",
    "name",
    "description",
    "triggers",
    "system_prompt",
    "tools",
    "requires_approval",
    "background",
    "priority",
    "memory_context",
    "max_tool_calls",
  )
  for key in required:
    if key not in entry:
      raise ValueError(f"{source}: missing required field '{key}'")

  agent_id = entry["id"]
  if not isinstance(agent_id, str) or not agent_id.strip():
    raise ValueError(f"{source}: 'id' must be a non-empty string")
  if not _ID_PATTERN.match(agent_id):
    raise ValueError(
      f"{source}: 'id' must match {_ID_PATTERN.pattern} (lowercase snake_case)"
    )
  if agent_id != stem:
    raise ValueError(
      f"{source}: file name stem '{stem}' must match 'id' field '{agent_id}'"
    )

  if not isinstance(entry["name"], str) or not entry["name"].strip():
    raise ValueError(f"{source}: 'name' must be a non-empty string")
  if not isinstance(entry["description"], str):
    raise ValueError(f"{source}: 'description' must be a string")
  if not isinstance(entry["system_prompt"], str):
    raise ValueError(f"{source}: 'system_prompt' must be a string")

  triggers = entry["triggers"]
  if not isinstance(triggers, list) or not all(isinstance(t, str) for t in triggers):
    raise ValueError(f"{source}: 'triggers' must be a list of strings")

  tools = entry["tools"]
  if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
    raise ValueError(f"{source}: 'tools' must be a list of strings")

  for key in ("requires_approval", "background", "memory_context"):
    if not isinstance(entry[key], bool):
      raise ValueError(f"{source}: '{key}' must be a boolean")

  if not isinstance(entry["priority"], int):
    raise ValueError(f"{source}: 'priority' must be an integer")
  max_tc = entry["max_tool_calls"]
  if not isinstance(max_tc, int) or max_tc < 0:
    raise ValueError(f"{source}: 'max_tool_calls' must be an integer >= 0")


def load_agent_definitions() -> dict[str, dict[str, Any]]:
  """Load all *.json agent descriptors; raise ValueError on invalid data."""
  global _AGENTS_BY_ID
  if _AGENTS_BY_ID is not None:
    return _AGENTS_BY_ID

  directory = _agents_dir()
  if not directory.is_dir():
    raise FileNotFoundError(f"Agent definitions directory not found: {directory}")

  by_id: dict[str, dict[str, Any]] = {}
  for path in sorted(directory.glob("*.json")):
    stem = path.stem
    try:
      raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
      raise ValueError(f"{path.name}: invalid JSON: {e}") from e
    if not isinstance(raw, dict):
      raise ValueError(f"{path.name}: root must be a JSON object")
    _validate_entry(raw, path.name, stem)
    aid = raw["id"]
    if aid in by_id:
      raise ValueError(f"Duplicate agent id '{aid}' in {path.name} and another file")
    by_id[aid] = raw

  if not by_id:
    raise ValueError(f"No agent definitions found in {directory}")

  logger.info("Loaded %d agent definition(s): %s", len(by_id), ", ".join(sorted(by_id)))
  _AGENTS_BY_ID = by_id
  return _AGENTS_BY_ID


def list_agents() -> list[dict[str, Any]]:
  """All agents sorted by priority (desc), then id."""
  reg = load_agent_definitions()
  return sorted(reg.values(), key=lambda a: (-a["priority"], a["id"]))


def get_agent(agent_id: str) -> dict[str, Any] | None:
  return load_agent_definitions().get(agent_id)


def reset_agent_cache_for_tests() -> None:
  """Clear cached definitions (tests only)."""
  global _AGENTS_BY_ID
  _AGENTS_BY_ID = None
