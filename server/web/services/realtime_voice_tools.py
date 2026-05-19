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
# ---------------------------------------------------------------------------
# Google News RSS scraper (free, no key, returns real headlines)
# ---------------------------------------------------------------------------

def _fetch_google_news_rss_sync(query: str, num_results: int = 6) -> dict | None:
    """Fetch news results from Google News RSS. Returns None on failure."""
    try:
        encoded = query.replace(" ", "+")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return None
        items = channel.findall("item")
        results = []
        for item in items[:num_results]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            source_el = item.find("{https://news.google.com}source") or item.find("source")
            source_name = source_el.text.strip() if source_el is not None and source_el.text else ""
            pub_date = (item.findtext("pubDate") or "").strip()
            if title:
                snippet = title
                if source_name:
                    snippet += f" ({source_name})"
                if pub_date:
                    snippet += f" — {pub_date[:22]}"
                results.append({"title": title, "url": link, "snippet": snippet})
        if not results:
            return None
        return {
            "source": "google_news_rss",
            "query": query,
            "results": results,
        }
    except Exception as exc:
        logger.warning("Google News RSS failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# DuckDuckGo HTML scraper (free, no key, general web results)
# ---------------------------------------------------------------------------

def _fetch_ddg_html_sync(query: str, num_results: int = 5) -> dict | None:
    """Scrape DuckDuckGo HTML search for snippet-level results. Returns None on failure."""
    import re
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            )
            resp.raise_for_status()
        html = resp.text
        # Extract result snippets using regex on DDG HTML structure
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
        # Strip HTML tags from snippets
        def strip_tags(s: str) -> str:
            return re.sub(r"<[^>]+>", "", s).strip()
        results = []
        for i, snip in enumerate(snippets[:num_results]):
            title = strip_tags(titles[i]) if i < len(titles) else ""
            snippet = strip_tags(snip)
            if snippet:
                results.append({"title": title, "snippet": snippet})
        if not results:
            return None
        return {"source": "duckduckgo_html", "query": query, "results": results}
    except Exception as exc:
        logger.warning("DDG HTML scrape failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Web search — Brave (paid) → Google News RSS → DDG HTML → DDG JSON instant
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

    # Fallback 1: Google News RSS — best for any news/current-events query
    news_kw = any(w in query.lower() for w in (
        "news", "latest", "today", "recent", "update", "headline", "breaking",
        "ipl", "cricket", "football", "match", "score", "politics", "election",
        "market", "stock", "economy", "trump", "modi", "government",
    ))
    if news_kw:
        gnews = _fetch_google_news_rss_sync(query, num_results)
        if gnews:
            return gnews

    # Fallback 2: DuckDuckGo HTML scraper — general web results
    ddg_html = _fetch_ddg_html_sync(query, num_results)
    if ddg_html:
        return ddg_html

    # Fallback 3: DuckDuckGo Instant Answer JSON (factual lookups only)
    try:
        with httpx.Client(timeout=6.0, follow_redirects=True) as client:
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

        if quick or related:
            return {
                "source": "duckduckgo",
                "query": query,
                "quick_answer": quick,
                "results": related,
            }
    except Exception as exc:
        logger.warning("DDG JSON fallback failed: %s", exc)

    # Try Google News RSS for any query as last resort
    gnews_any = _fetch_google_news_rss_sync(query, num_results)
    if gnews_any:
        return gnews_any

    return {
        "source": "unavailable",
        "query": query,
        "note": "All search backends failed. Add BRAVE_SEARCH_API_KEY for reliable results.",
        "results": [],
    }


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
            },
            "required": ["pending_id"],
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
    return json.dumps({"error": "request_failed", "status": exc.status_code, "detail": detail[:4000]})


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
            mid = args.get("meeting_id")
            meeting_id = str(mid).strip() if mid else None
            payload = process_assistant_intent(
                message=msg,
                user_id=user_id,
                meeting_id=meeting_id,
                source="voice_realtime",
            )
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
            pid = str(args.get("pending_id") or "").strip()
            if not pid:
                return json.dumps({"error": "pending_id_required"})
            try:
                out = svc_approve_pending_action(pid, user_id)
            except HTTPException as e:
                return _http_exc_to_tool_json(e)
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
