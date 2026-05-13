"""Realtime voice tool definitions and server-side execution (read-only, user-scoped)."""

from __future__ import annotations

import json
import logging
from typing import Any

from services.briefing_context import build_briefing_context_dict
from services.mem0_service import mem0_disabled_globally, search_context_for_prompt

logger = logging.getLogger(__name__)

# OpenAI Realtime function tools (JSON schema parameters).
REALTIME_VOICE_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "memory_search",
        "description": (
            "Search the user's long-term Mem0 memory for facts, notes, reminders, and past context. "
            "Use for questions like what they saved, asked before, or discussed earlier."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query (e.g. notes from last week).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "get_briefing_context",
        "description": (
            "Get the same structured morning-brief style bundle as the device UI: greeting, "
            "calendar days, tasks/commitments, recent meetings, Mem0 snippet, pending assistant "
            "actions, Gmail preview. Use when the user asks for a morning brief or overview of their day."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "Calendar window in days from today (1-14). Default 1.",
                },
            },
        },
    },
]


def execute_realtime_voice_tool(
    *,
    user_id: str,
    actor: dict,
    name: str,
    arguments_json: str,
) -> str:
    """
    Run a voice-realtime tool and return a **string** suitable for OpenAI function_call_output.
    """
    try:
        args = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError as e:
        return json.dumps({"error": "invalid_tool_arguments", "detail": str(e)})

    if not (user_id or "").strip():
        return json.dumps({"error": "unauthenticated"})

    try:
        if name == "memory_search":
            if mem0_disabled_globally():
                return json.dumps({"mem0_enabled": False, "snippet": None})
            q = str(args.get("query") or "").strip()
            if not q:
                return json.dumps({"error": "query_required"})
            blob = search_context_for_prompt(user_id, q)
            return json.dumps(
                {
                    "mem0_enabled": True,
                    "snippet": (blob[:8000] if blob else None),
                },
                default=str,
            )

        if name == "get_briefing_context":
            da = int(args.get("days_ahead") or 1)
            bundle = build_briefing_context_dict(
                actor=actor,
                user_id=user_id,
                days_ahead=da,
                mem0_cap=1200,
            )
            # Compact for model: dump as JSON string (model reads structured data).
            return json.dumps(bundle, default=str)

        return json.dumps({"error": "unknown_tool", "name": name})
    except Exception:
        logger.exception("realtime_voice_tool failed name=%s", name)
        return json.dumps({"error": "tool_execution_failed", "name": name})
