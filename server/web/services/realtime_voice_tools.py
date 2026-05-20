"""Realtime voice tool definitions and server-side execution (user-scoped)."""

from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from fastapi import HTTPException

from assistant_service import (
    approve_pending_action as svc_approve_pending_action,
    list_pending_actions_for_user,
    process_assistant_intent,
    reject_pending_action as svc_reject_pending_action,
)
from services.briefing_context import build_briefing_context_dict
from services.mem0_service import (
    ingest_voice_explicit_memory,
    maybe_ingest_assistant_turn,
    maybe_ingest_calendar_snapshot,
    maybe_ingest_gmail_snapshot,
    mem0_disabled_globally,
    mem0_runtime_ready,
    search_context_for_prompt,
)

logger = logging.getLogger(__name__)

_MAX_ASSISTANT_INTENT_JSON = 24000

# ---------------------------------------------------------------------------
# Weather helper (sync — called from run_in_executor)
# ---------------------------------------------------------------------------
_WMO_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Rain showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}


def _fetch_weather_sync() -> dict:
    lat = float(os.getenv("WEATHER_LAT", "12.9716"))
    lon = float(os.getenv("WEATHER_LON", "77.5946"))
    city = os.getenv("WEATHER_CITY", "Bengaluru")

    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,apparent_temperature,weathercode,"
        "relative_humidity_2m,wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min"
        "&timezone=auto&forecast_days=1"
    )
    aqi_url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={lat}&longitude={lon}&current=us_aqi"
    )

    result: dict = {
        "city": city,
        "temperature_c": None,
        "feels_like_c": None,
        "condition": "Unknown",
        "high_c": None,
        "low_c": None,
        "humidity_pct": None,
        "wind_kph": None,
        "aqi": None,
    }
    with httpx.Client(timeout=8.0) as client:
        try:
            data = client.get(weather_url).raise_for_status().json()
            curr = data.get("current", {})
            daily = data.get("daily", {})
            temp = curr.get("temperature_2m")
            result["temperature_c"] = round(temp) if temp is not None else None
            feels = curr.get("apparent_temperature")
            result["feels_like_c"] = round(feels) if feels is not None else None
            code = curr.get("weathercode")
            result["condition"] = _WMO_CODES.get(int(code), "Unknown") if code is not None else "Unknown"
            highs = daily.get("temperature_2m_max", [])
            lows = daily.get("temperature_2m_min", [])
            result["high_c"] = round(highs[0]) if highs else None
            result["low_c"] = round(lows[0]) if lows else None
            hum = curr.get("relative_humidity_2m")
            result["humidity_pct"] = int(hum) if hum is not None else None
            wind = curr.get("wind_speed_10m")
            result["wind_kph"] = round(wind, 1) if wind is not None else None
        except Exception as exc:
            logger.warning("Weather fetch failed: %s", exc)

        try:
            aqi_data = client.get(aqi_url).raise_for_status().json()
            us_aqi = aqi_data.get("current", {}).get("us_aqi")
            result["aqi"] = int(us_aqi) if us_aqi is not None else None
        except Exception as exc:
            logger.warning("AQI fetch failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# News helper (sync — called from run_in_executor)
# BBC News RSS: free, no API key, globally accessible.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Web search helper (sync — called from run_in_executor)
# Primary: Brave Search API (BRAVE_SEARCH_API_KEY env var, free tier 2000/mo)
# Fallback: DuckDuckGo Instant Answer API (no key, limited to factual answers)
# ---------------------------------------------------------------------------

def _fetch_web_search_sync(query: str, num_results: int = 5) -> dict:
    brave_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()

    if brave_key:
        # Brave Search API — structured web results
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": num_results, "text_decorations": False, "search_lang": "en"},
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": brave_key,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            web_results = data.get("web", {}).get("results", [])
            results = []
            for r in web_results[:num_results]:
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("description", ""),
                })

            # Include infobox if present (concise factual answers)
            infobox = data.get("infobox", {})
            quick_answer = None
            if infobox:
                desc = infobox.get("description") or infobox.get("long_desc") or ""
                if desc:
                    quick_answer = desc[:400]

            return {
                "source": "brave",
                "query": query,
                "quick_answer": quick_answer,
                "results": results,
            }
        except Exception as exc:
            logger.warning("Brave search failed, falling back to DDG: %s", exc)

    # Fallback: DuckDuckGo Instant Answer API (facts/definitions only, no full web results)
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_redirect": 1, "no_html": 1, "skip_disambig": 1},
                headers={"User-Agent": "MeetingBox/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()

        abstract = (data.get("AbstractText") or "").strip()
        answer = (data.get("Answer") or "").strip()
        definition = (data.get("Definition") or "").strip()
        quick = answer or abstract or definition or None

        related = []
        for t in (data.get("RelatedTopics") or [])[:4]:
            text = t.get("Text", "") if isinstance(t, dict) else ""
            if text:
                related.append({"snippet": text[:200]})

        if not quick and not related:
            return {
                "source": "duckduckgo",
                "query": query,
                "note": "No instant answer found. Set BRAVE_SEARCH_API_KEY for full web search.",
                "results": [],
            }

        return {
            "source": "duckduckgo",
            "query": query,
            "quick_answer": quick,
            "results": related,
        }
    except Exception as exc:
        logger.warning("DDG fallback also failed: %s", exc)
        return {"error": "search_unavailable", "detail": str(exc)}


# ---------------------------------------------------------------------------
# News helper (sync — called from run_in_executor)
# BBC News RSS: free, no API key, globally accessible.
# ---------------------------------------------------------------------------
_NEWS_FEEDS: dict[str, str] = {
    "top":        "https://feeds.bbci.co.uk/news/rss.xml",
    "world":      "https://feeds.bbci.co.uk/news/world/rss.xml",
    "technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "business":   "https://feeds.bbci.co.uk/news/business/rss.xml",
    "science":    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "health":     "https://feeds.bbci.co.uk/news/health/rss.xml",
}


def _fetch_news_sync(category: str = "top", limit: int = 6) -> dict:
    url = _NEWS_FEEDS.get(category.lower().strip(), _NEWS_FEEDS["top"])
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "MeetingBox/1.0"})
            resp.raise_for_status()
        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        items = (channel.findall("item") if channel is not None else [])[:limit]
        headlines = []
        for item in items:
            title = (item.findtext("title") or "").strip()
            desc = (item.findtext("description") or "").strip()
            if title:
                headlines.append({"title": title, "summary": desc[:200] if desc else None})
        return {
            "source": "BBC News",
            "category": category,
            "headlines": headlines,
            "count": len(headlines),
        }
    except Exception as exc:
        logger.warning("News fetch failed: %s", exc)
        return {"error": "news_unavailable", "detail": str(exc)}

# Whitelist returned to the device; Kivy `goto_screen` must support the name.
REALTIME_DEVICE_NAV_SCREENS = frozenset(
    {"home", "calendar", "emails", "meetings", "morning_brief", "settings", "mic_test"}
)

_SIDE_EFFECT_HINTS = (
    "send",
    "email",
    "invite",
    "calendar",
    "schedule",
    "remind",
    "reminder",
    "create",
    "update",
    "delete",
    "cancel",
    "book",
)


def _truth_status_for_assistant_intent(payload: dict[str, Any]) -> dict[str, Any]:
    pending = payload.get("pending_actions")
    rows = pending if isinstance(pending, list) else []
    pending_ids = [
        str(r.get("id") or "").strip()
        for r in rows
        if isinstance(r, dict) and str(r.get("id") or "").strip()
    ]
    committed = len(pending_ids) == 0
    return {
        "writes_committed": committed,
        "pending_count": len(pending_ids),
        "pending_ids": pending_ids,
        "note": (
            "Writes are queued only; none executed yet."
            if not committed
            else "No queued writes in this response."
        ),
    }


def _resolve_pending_for_approval(user_id: str, requested_pending_id: str) -> tuple[str | None, dict[str, Any] | None]:
    pid = (requested_pending_id or "").strip()
    if pid:
        return pid, None

    pending = list_pending_actions_for_user(user_id)
    if len(pending) == 1 and isinstance(pending[0], dict):
        only = str(pending[0].get("id") or "").strip()
        if only:
            return only, None

    if not pending:
        return None, {
            "error": "no_pending_actions",
            "detail": "There are no queued actions to approve right now.",
            "truth_status": {"writes_committed": False, "note": "No write executed."},
        }

    choices = []
    for row in pending[:5]:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        if not rid:
            continue
        choices.append(
            {
                "id": rid,
                "brief_label": str(row.get("brief_label") or "").strip(),
                "tool_name": str(row.get("tool_name") or "").strip(),
            }
        )
    return None, {
        "error": "pending_id_required",
        "detail": "Multiple queued actions found; specify which one to approve.",
        "pending_choices": choices,
        "truth_status": {"writes_committed": False, "note": "No write executed."},
    }

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
        "name": "memory_remember",
        "description": (
            "Save a stable fact the user wants you to remember across future sessions: preferences, deadlines, "
            "names, ongoing projects, commitments they asked you to retain, or corrections to what you knew. "
            "Call when they say remember, don't forget, note that, keep in mind, etc. "
            "Pass one short self-contained factual sentence (no chit-chat)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "One clear sentence to store (e.g. \"User prefers morning meetings before 9am\").",
                },
                "context_note": {
                    "type": "string",
                    "description": "Optional 1-line reason or topic label (e.g. \"from voice session about travel\").",
                },
            },
            "required": ["fact"],
        },
    },
    {
        "type": "function",
        "name": "get_briefing_context",
        "description": (
            "Primary data bundle for voice: Google Calendar events are under days[date].meetings; "
            "meetings_recent = last recordings from meetings.db (titles + summary excerpts); "
            "commitments = tasks/reminders; gmail_preview.recent_messages = recent inbox rows; "
            "mem0_snippet = long-term memory; pending_assistant = queued actions. "
            "Call this (and memory_search if needed) before answering schedule/email/task questions. "
            "Explain results to the user in natural conversational speech—not as raw field names—unless they ask for exact details. "
            "Use days_ahead>=2 if the user said tomorrow or the next day."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": (
                        "Inclusive day count starting today in the user's timezone: 1=today only, "
                        "2=today+tomorrow, up to 14. Omit to default to today+tomorrow for voice."
                    ),
                },
            },
        },
    },
    {
        "type": "function",
        "name": "assistant_intent",
        "description": (
            "Run the user's request through MeetingBox assistants (calendar, Gmail, commitments, meeting memory, device). "
            "Pass their exact spoken intent as plain text. The JSON may contain assistant_message—the user should never hear "
            "that text read robotically; summarize and paraphrase facts in natural conversational speech. "
            "Use pending_actions data for approval flows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The user's request in natural language (what they asked for by voice).",
                },
                "meeting_id": {
                    "type": "string",
                    "description": "Optional meeting/recording ID if explicitly relevant.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "type": "function",
        "name": "list_pending_actions",
        "description": (
            "List assistant actions queued for approval (calendar create, outbound email send, "
            "device commands). Each item has id and brief_label suitable to read aloud."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "approve_pending_action",
        "description": (
            "Execute ONE queued write (calendar/email/device) only after the user has clearly confirmed aloud "
            "(e.g. yes, go ahead, confirm, approve) that they want THIS specific pending action. "
            "If they have not confirmed, do not call—summarize the action and ask them first. "
            "pending_id comes from list_pending_actions or assistant_intent pending_actions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pending_id": {
                    "type": "string",
                    "description": "The pending action UUID from the assistant queue.",
                },
                "confirmed_by_user": {
                    "type": "boolean",
                    "description": "Must be true only after explicit verbal confirmation.",
                },
                "confirmation_phrase": {
                    "type": "string",
                    "description": "Exact short confirmation heard from user (e.g. 'yes go ahead').",
                },
            },
            "required": ["pending_id", "confirmed_by_user", "confirmation_phrase"],
        },
    },
    {
        "type": "function",
        "name": "reject_pending_action",
        "description": "Cancel a queued action after the user declines.",
        "parameters": {
            "type": "object",
            "properties": {
                "pending_id": {
                    "type": "string",
                    "description": "The pending action UUID to reject.",
                },
            },
            "required": ["pending_id"],
        },
    },
    {
        "type": "function",
        "name": "navigate_device_ui",
        "description": (
            "Open a main screen on the tabletop device (Kivy UI). Use when the user asks to open/show/go to "
            "calendar, email/inbox, meetings/tasks, home, morning brief, settings, or microphone test. "
            "Does not fetch data — combine with get_briefing_context or assistant_intent when they also want information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "screen": {
                    "type": "string",
                    "enum": sorted(REALTIME_DEVICE_NAV_SCREENS),
                    "description": "Registered device screen id (matches device-ui Screen.name).",
                },
            },
            "required": ["screen"],
        },
    },
    {
        "type": "function",
        "name": "web_search",
        "description": (
            "Search the internet for up-to-date information. Use this for: current events and news, "
            "recent facts that may have changed (prices, sports results, election outcomes, product releases), "
            "anything the user asks about using words like 'latest', 'current', 'today', 'right now', "
            "or any topic where your training knowledge might be out of date. "
            "Do NOT use for questions you can answer from training knowledge (history, science, math, etc.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Concise search query (e.g. 'latest iPhone release 2026', 'India vs Australia cricket score today').",
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "get_weather",
        "description": (
            "Fetch current weather conditions for the device location: temperature, feels-like, "
            "condition (sunny/cloudy/rain etc.), today's high and low, humidity, wind speed, and air quality index. "
            "Call whenever the user asks about the weather, temperature, or whether to carry an umbrella."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "get_news",
        "description": (
            "Fetch the latest top news headlines (BBC News). "
            "Call when the user asks about news, headlines, what's happening in the world, or trending stories. "
            "Optional category: top (default), world, technology, business, science, health."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["top", "world", "technology", "business", "science", "health"],
                    "description": "News category to fetch. Defaults to 'top' for general headlines.",
                },
            },
        },
    },
]


def _http_exc_to_tool_json(exc: HTTPException) -> str:
    detail = exc.detail
    if not isinstance(detail, str):
        try:
            detail = json.dumps(detail)
        except TypeError:
            detail = str(detail)
    return json.dumps(
        {
            "error": "request_failed",
            "status": exc.status_code,
            "detail": detail[:4000],
            "truth_status": {"writes_committed": False, "note": "No write executed."},
        }
    )


def _assistant_intent_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, default=str)
    if len(raw) <= _MAX_ASSISTANT_INTENT_JSON:
        return raw
    pending = payload.get("pending_actions")
    slim: dict[str, Any] = {
        "truncated": True,
        "assistant_message": payload.get("assistant_message"),
        "routed_agent_id": payload.get("routed_agent_id"),
        "routing_method": payload.get("routing_method"),
        "pending_actions": pending if isinstance(pending, list) else [],
        "tool_results_preview": [],
    }
    tr = payload.get("tool_results")
    if isinstance(tr, list):
        slim["tool_results_preview"] = tr[:8]
        slim["tool_results_omitted"] = max(0, len(tr) - 8)
    s2 = json.dumps(slim, default=str)
    if len(s2) <= _MAX_ASSISTANT_INTENT_JSON:
        return s2
    am = str(slim.get("assistant_message") or "")[:4500]
    slim2 = dict(slim)
    slim2["assistant_message"] = am
    slim2["pending_actions"] = (slim.get("pending_actions") or [])[:25]
    return json.dumps(slim2, default=str)[:_MAX_ASSISTANT_INTENT_JSON]


def _needs_task_clarification(message: str) -> str | None:
    m = " ".join((message or "").lower().split())
    if not any(k in m for k in _SIDE_EFFECT_HINTS):
        return None
    # Calendar invite with missing core fields.
    if any(k in m for k in ("invite", "calendar", "schedule", "book")):
        has_time = any(k in m for k in ("tomorrow", "today", " at ", "am", "pm", "next ", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))
        has_title = any(k in m for k in ("about", "for ", "called ", "titled "))
        if not has_time or not has_title:
            return "I can do that. What time should I schedule it, and what should I title the invite?"
    # Email send with no recipient hint.
    if "email" in m or "send" in m:
        if "@" not in m and not any(k in m for k in ("to ", "recipient", "cc ", "bcc ")):
            return "Sure. Who should this email go to, and what tone should I use?"
    # Reminder without time cue.
    if "remind" in m or "reminder" in m:
        if not any(k in m for k in ("at ", "tomorrow", "today", "next ", "on ", "in ")):
            return "Got it. What should I remind you about, and when should I remind you?"
    return None


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
            # Use the runtime-ready check (not just the disable-env check):
            # if MEM0_API_KEY is missing or the Memory() client failed to
            # initialize, we must report mem0_enabled=false so the model
            # tells the user "memory is unavailable right now" instead of
            # truthfully but misleadingly saying "I have nothing saved".
            if not mem0_runtime_ready():
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

        if name == "memory_remember":
            fact = str(args.get("fact") or "").strip()
            ctxn = args.get("context_note")
            cns = str(ctxn).strip() if ctxn else None
            out = ingest_voice_explicit_memory(user_id, fact=fact, context_note=cns)
            return json.dumps(out, default=str)

        if name == "get_briefing_context":
            raw_da = args.get("days_ahead")
            if raw_da in (None, ""):
                da = 2
            else:
                try:
                    da = int(raw_da)
                except (TypeError, ValueError):
                    da = 2
            bundle = build_briefing_context_dict(
                actor=actor,
                user_id=user_id,
                days_ahead=da,
                mem0_cap=2800,
                gmail_preview_max=15,
            )
            try:
                days = bundle.get("days") or {}
                cal_rows: list = []
                for day in days.values():
                    cal_rows.extend(day.get("meetings") or day.get("events") or [])
                maybe_ingest_calendar_snapshot(
                    user_id, {"events": cal_rows, "count": len(cal_rows)}
                )
            except Exception:
                logger.debug("get_briefing_context: calendar ingest failed", exc_info=True)
            try:
                gp = bundle.get("gmail_preview") or {}
                msgs = list(gp.get("recent_messages") or [])
                if not msgs and gp.get("top"):
                    msgs = [gp["top"]]
                maybe_ingest_gmail_snapshot(user_id, {"messages": msgs, "count": len(msgs)})
            except Exception:
                logger.debug("get_briefing_context: gmail ingest failed", exc_info=True)
            return json.dumps(bundle, default=str)

        if name == "assistant_intent":
            msg = str(args.get("message") or "").strip()
            if not msg:
                return json.dumps({"error": "message_required"})
            clarify = _needs_task_clarification(msg)
            if clarify:
                return json.dumps(
                    {
                        "assistant_message": clarify,
                        "pending_actions": [],
                        "tool_results": [],
                        "requires_clarification": True,
                    },
                    default=str,
                )
            mid = args.get("meeting_id")
            meeting_id = str(mid).strip() if mid else None
            payload = process_assistant_intent(
                message=msg,
                user_id=user_id,
                meeting_id=meeting_id,
                source="voice_realtime",
            )
            if isinstance(payload, dict):
                payload["truth_status"] = _truth_status_for_assistant_intent(payload)
            try:
                maybe_ingest_assistant_turn(
                    user_id,
                    user_message=msg,
                    assistant_reply=str(payload.get("assistant_message") or "")[:8000],
                    routed_agent_id=str(payload.get("routed_agent_id") or "").strip() or None,
                    meeting_id=meeting_id,
                )
            except Exception:
                logger.debug("assistant_intent Mem0 ingest skipped", exc_info=True)
            return _assistant_intent_json(payload)

        if name == "list_pending_actions":
            rows = list_pending_actions_for_user(user_id)
            return json.dumps({"pending": rows, "count": len(rows)}, default=str)

        if name == "approve_pending_action":
            pid_raw = str(args.get("pending_id") or "").strip()
            pid, resolve_err = _resolve_pending_for_approval(user_id, pid_raw)
            if resolve_err:
                return json.dumps(resolve_err, default=str)
            if args.get("confirmed_by_user") is not True:
                return json.dumps(
                    {
                        "error": "confirmation_required",
                        "detail": "Explicit user confirmation is required before executing actions.",
                        "truth_status": {
                            "writes_committed": False,
                            "note": "No write executed because confirmation is missing.",
                        },
                    }
                )
            phrase = str(args.get("confirmation_phrase") or "").strip().lower()
            allowed = ("yes", "confirm", "go ahead", "approve", "do it", "send it")
            if not phrase or not any(a in phrase for a in allowed):
                return json.dumps(
                    {
                        "error": "confirmation_phrase_required",
                        "detail": "Provide the user's explicit confirmation phrase.",
                        "truth_status": {
                            "writes_committed": False,
                            "note": "No write executed because confirmation phrase was insufficient.",
                        },
                    }
                )
            try:
                out = svc_approve_pending_action(pid, user_id)
            except HTTPException as e:
                return _http_exc_to_tool_json(e)
            if isinstance(out, dict):
                ok = str(out.get("status") or "").lower() == "completed"
                out["truth_status"] = {
                    "writes_committed": ok,
                    "note": "Write executed successfully." if ok else "Write did not execute successfully.",
                }
            return json.dumps(out, default=str)

        if name == "reject_pending_action":
            pid = str(args.get("pending_id") or "").strip()
            if not pid:
                return json.dumps({"error": "pending_id_required"})
            try:
                out = svc_reject_pending_action(pid, user_id)
            except HTTPException as e:
                return _http_exc_to_tool_json(e)
            return json.dumps(out, default=str)

        if name == "navigate_device_ui":
            raw = str(args.get("screen") or "").strip().lower().replace(" ", "_")
            if raw in ("inbox", "mail", "gmail"):
                raw = "emails"
            if raw in ("task", "tasks", "action_items", "todo", "todos"):
                raw = "meetings"
            if raw not in REALTIME_DEVICE_NAV_SCREENS:
                return json.dumps(
                    {
                        "error": "invalid_screen",
                        "allowed": sorted(REALTIME_DEVICE_NAV_SCREENS),
                    }
                )
            return json.dumps({"ok": True, "device_navigate": raw})

        if name == "web_search":
            query = str(args.get("query") or "").strip()
            if not query:
                return json.dumps({"error": "query_required"})
            result = _fetch_web_search_sync(query)
            return json.dumps(result)

        if name == "get_weather":
            result = _fetch_weather_sync()
            return json.dumps(result)

        if name == "get_news":
            category = str(args.get("category") or "top").strip().lower()
            if category not in _NEWS_FEEDS:
                category = "top"
            result = _fetch_news_sync(category=category)
            return json.dumps(result)

        return json.dumps({"error": "unknown_tool", "name": name})
    except Exception:
        logger.exception("realtime_voice_tool failed name=%s", name)
        return json.dumps({"error": "tool_execution_failed", "name": name})
