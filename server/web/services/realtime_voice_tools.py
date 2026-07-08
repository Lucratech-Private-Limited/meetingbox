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
from services.approval import require_user_approval
from services.briefing_context import build_briefing_context_dict
from tools.memory_tool import memory_fetch_meeting, memory_search_meetings
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
    {"home", "voice_session", "calendar", "emails", "meetings", "tasks",
     "morning_brief", "settings", "mic_test"}
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

def _normalise_hhmm(value: str | None) -> str | None:
    """Coerce a spoken/typed time into 24-hour HH:MM, or None if unparseable.

    Accepts "15:30", "3:30 PM", "3 PM", "3pm", "9:05am", "15.30".
    """
    s = (value or "").strip().lower().replace(".", ":")
    if not s:
        return None
    from datetime import datetime as _dt
    for fmt in ("%H:%M", "%I:%M %p", "%I %p", "%I:%M%p", "%I%p", "%H"):
        try:
            return _dt.strptime(s.upper() if "%p" in fmt else s, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


# OpenAI Realtime function tools (JSON schema parameters).
REALTIME_VOICE_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "memory_search",
        "description": (
            "Search the user's long-term Mem0 memory for facts, preferences, past conversations, and prior context. "
            "Use for 'what did I tell you about X', 'do you remember Y', 'what are my preferences for Z'. "
            "Do NOT use this for 'show my notes' / 'list my notes' / 'what notes do I have' — use note_list for those."
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
            "Save anything that should persist across future sessions so you act like a personal assistant who "
            "knows this user. This covers TWO kinds of memory:\n"
            "1) FACTS — preferences, deadlines, names, relationships, ongoing projects, interests, choices, "
            "corrections to what you knew.\n"
            "2) STANDING DIRECTIVES — how the user wants you to behave/speak: tone, persona, style, verbosity, "
            "what to call them, things to always or never do.\n"
            "Call this NOT ONLY for explicit 'remember / don't forget / note that / keep in mind' requests, but "
            "ALSO whenever the user states a lasting preference or instruction, even without those words — e.g. "
            "'talk to me in a sarcastic tone from now on', 'always keep answers short', 'never call me sir', "
            "'I prefer morning meetings', 'call me Vivek', 'from now on ...', 'I like ...', 'I hate ...'. "
            "Cues like 'from now on', 'always', 'never', 'I prefer', 'going forward', 'stop doing X' mean it is "
            "a standing preference — store it AND apply it immediately. "
            "Do NOT store one-off requests scoped to the current task ('make THIS email formal') or transient "
            "chit-chat. Pass one short self-contained sentence written so a future session understands it with "
            "no other context (e.g. \"User wants the assistant to always speak in a sarcastic tone.\")."
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
            "Use days_ahead>=2 if the user said tomorrow or the next day. "
            "When the user asks about a specific future date (e.g. 'next Tuesday', 'this Friday', 'next week'), "
            "resolve it to YYYY-MM-DD and pass it as 'date' — do NOT omit it and rely on days_ahead alone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": (
                        "Inclusive day count starting from 'date' (or today if date omitted): "
                        "1=single day, 2=two days, up to 14. Use 7 for 'next week'. Defaults to 2."
                    ),
                },
                "date": {
                    "type": "string",
                    "description": (
                        "Start date in YYYY-MM-DD format. REQUIRED when the user asks about a specific "
                        "day other than today (e.g. 'next Tuesday' -> that Tuesday's date, "
                        "'this Friday' -> this Friday's date, 'next week' -> next Monday's date). "
                        "Omit only for 'today' or 'upcoming' queries."
                    ),
                },
            },
        },
    },
    {
        "type": "function",
        "name": "get_sent_emails",
        "description": (
            "Retrieve emails the user sent (outbox / sent mail). Use for: 'who did I email last', "
            "'find my email to Rahul', 'draft a follow-up to the email I sent', 'what did I send today'. "
            "Do NOT use get_briefing_context for sent mail — it shows INBOX only, never sent mail."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional Gmail search refinement, e.g. 'to:rahul' or 'subject:invoice'. Leave empty to get latest sent.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of sent emails to return (1–20, default 5).",
                },
            },
            "required": [],
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
            "calendar, email/inbox, tasks/todos, meetings, home, morning brief, settings, or microphone test. "
            "Also use screen='voice_session' to return to the audio transcription / live listening screen — "
            "this is the default conversational home; go there when a temporary experience like the morning "
            "brief is finished or the user no longer wants it. "
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
                "target_date": {
                    "type": "string",
                    "description": (
                        "ISO 8601 date (YYYY-MM-DD) to open the calendar on. "
                        "Only used when screen=calendar. "
                        "Always resolve relative expressions such as 'next Tuesday', 'this Friday', "
                        "'31st of May', or 'tomorrow' to an actual YYYY-MM-DD date before passing here."
                    ),
                },
                "target_tab": {
                    "type": "string",
                    "description": (
                        "Section/tab to activate within the screen. "
                        "For screen=tasks: today | upcoming | unfinished | unplanned. "
                        "For screen=emails: today | all | unread | sent | drafts. "
                        "For screen=morning_brief: schedule | tasks | emails | next | previous "
                        "(switch the carousel card; use during a guided morning briefing)."
                    ),
                },
            },
            "required": ["screen"],
        },
    },
    {
        "type": "function",
        "name": "show_meeting_summary",
        "description": (
            "Open the on-screen MEETING / NOTE SUMMARY page for a specific recorded meeting or note so "
            "the user can READ it on the device. Use this — NOT assistant_intent — whenever the user "
            "EXPLICITLY asks to SHOW / OPEN / PULL UP / DISPLAY / 'bring up' a meeting or note summary on "
            "screen (e.g. 'show me the summary of the board meeting', 'pull up my note about Project "
            "Atlas', 'open the summary of my last meeting', 'display the notes from the investor call'). "
            "The server runs ranked, context-aware retrieval over the user's recordings, then the device "
            "opens the full summary page (title, AI summary, decisions, action items). Pass the user's "
            "description as `query` — treat it as keywords/context (participants, topic, project, event, "
            "date), NEVER as an exact title. When the call returns ok with a meeting, say exactly ONE "
            "short line — e.g. 'Here's your board-meeting summary from June 17.' — and do NOT read the "
            "summary body aloud; the screen is the reading surface. If it returns needs_clarification, "
            "read the clarification question aloud and let the user choose. If it returns found=false, "
            "tell the user you couldn't find that recording. For spoken-only recall where the user is NOT "
            "asking to see it on screen ('what was decided?', 'who was in the meeting?', 'summarize my "
            "last meeting'), keep using assistant_intent instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Free-text describing which recording to open — participants, topic, project, "
                        "event, or date context the user gave. Keywords/context, never an exact title."
                    ),
                },
                "session_type": {
                    "type": "string",
                    "enum": ["meeting", "note"],
                    "description": (
                        "Optional: restrict to a meeting or a personal note when the user is specific "
                        "('my note about X' -> note; 'the meeting with Y' -> meeting)."
                    ),
                },
                "meeting_id": {
                    "type": "string",
                    "description": (
                        "Optional exact recording id when already known (e.g. from a prior "
                        "assistant_intent result). When given, skips search and opens it directly."
                    ),
                },
            },
            "required": ["query"],
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
    {
        "type": "function",
        "name": "convert_currency",
        "description": (
            "Convert an amount between two currencies at live daily rates (open.er-api.com). "
            "Call instantly whenever the user asks 'how much is X in Y', 'convert 100 USD to INR', "
            "'rupee to dollar', 'price in euros', etc. Accepts common names (dollar, rupee, euro, "
            "pound, yen) or ISO codes (USD, INR, EUR, GBP, JPY)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "Numeric amount to convert. Defaults to 1 if the user just asks for a rate.",
                },
                "from": {
                    "type": "string",
                    "description": "Source currency (e.g. 'USD', 'rupee', '$').",
                },
                "to": {
                    "type": "string",
                    "description": "Target currency (e.g. 'INR', 'euro', '£').",
                },
            },
            "required": ["from", "to"],
        },
    },
    {
        "type": "function",
        "name": "get_stock_price",
        "description": (
            "Get a live stock / index quote from Yahoo Finance. Works for US tickers "
            "(AAPL, TSLA, MSFT), Indian NSE/BSE tickers (RELIANCE.NS, TATASTEEL.NS, INFY.BO), and "
            "indices (^NSEI for Nifty, ^BSESN for Sensex, ^GSPC for S&P 500). The agent should "
            "accept either the ticker symbol OR the plain company name — common Indian names like "
            "'Tata Steel', 'Reliance', 'TCS', 'Infosys', 'Nifty', 'Sensex' resolve automatically. "
            "Returns the current price, % change vs previous close, currency, and exchange. "
            "Use this tool — DO NOT fall back to web_search — for any price / quote / 'how much is X "
            "trading at' / 'XYZ share price' query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker symbol or recognizable company / index name (e.g. 'AAPL', 'TATASTEEL.NS', 'Tata Steel', 'Nifty', 'Sensex').",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "type": "function",
        "name": "get_sports_score",
        "description": (
            "Get the latest score / result for a sports match (cricket, football, basketball, etc.). "
            "Call whenever the user asks about a match, game, score, result, or league standing — "
            "'India vs Australia score', 'Man United latest match', 'IPL today', 'world cup standings'. "
            "Pass the user's exact phrasing as the query — the tool augments it for relevance."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-form match / team / league query (e.g. 'India vs Australia ODI today', 'Premier League standings').",
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "find_research_paper",
        "description": (
            "Search Semantic Scholar for academic / research papers by free-text query. "
            "Use this — NOT web_search — whenever the user asks about a research paper, citation, "
            "academic work, peer-reviewed study, conference paper (ICCV / NeurIPS / CVPR etc.), or "
            "wants citations / references on a topic. Returns title, authors, year, venue, "
            "citation count, abstract, DOI, and PDF link when available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Paper title, topic, or author name (e.g. 'Placeit3D language guided object placement', 'attention is all you need').",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many results to fetch (default 5, max 10).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "deep_research",
        "description": (
            "Multi-source web research with cited synthesis. Plans 3 focused sub-queries by default "
            "(shallow depth — fast and cheap), fetches results from each, and asks Claude to "
            "synthesize a single answer with [1] [2] citation markers and a source list. "
            "Use ONLY when the user explicitly asks to 'research X', 'deep dive on X', 'investigate X', "
            "'compare X and Y', or asks a question broad enough that one web_search won't cover it. "
            "Default to shallow depth. Upgrade only when the user says 'deep dive', 'comprehensive', "
            "'thorough', 'in-depth', or 'exhaustive'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The research topic in the user's words.",
                },
                "depth": {
                    "type": "string",
                    "enum": ["shallow", "medium", "deep"],
                    "description": "How thorough to go. Default 'shallow' (3 sub-queries, ~200 words). Only upgrade on explicit user cues.",
                },
            },
            "required": ["topic"],
        },
    },
    {
        "type": "function",
        "name": "create_task",
        "description": (
            "Add a single task / to-do / reminder to the user's task list. Use this for any phrasing like "
            "'add a task', 'remind me to', 'note that', 'add to my list', 'save as task', 'follow up on'. "
            "FAITHFULNESS RULES (strict): "
            "(1) title must be a ≤8-word paraphrase of what the user said — keep the verb and the object, "
            "drop filler. Never invent. "
            "(2) due_date only when the user explicitly mentions a date ('tomorrow', 'by Friday', "
            "'on the 15th'). Resolve to YYYY-MM-DD before passing. If no date, OMIT due_date — the task "
            "lands in the Unplanned bucket. "
            "(3) description is empty unless the user spoke a clear description. Never invent details. "
            "DUPLICATE CHECK: this tool checks for similar active tasks first. If one exists, it returns "
            "{warning: 'similar_task_exists', ...} WITHOUT creating. The agent must read out the existing "
            "task to the user and ask 'add anyway, or update the existing one?' before calling again "
            "with confirm_duplicate=true."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Task header — ≤8-word paraphrase of the user's request (e.g. 'Call John about proposal').",
                },
                "due_date": {
                    "type": "string",
                    "description": "Optional ISO date YYYY-MM-DD. Set ONLY when the user explicitly mentioned a date. Resolve 'tomorrow', 'Friday', 'next Monday' to a real date.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional detail. Only set if the user spoke an explicit description. Never invent.",
                },
                "confirm_duplicate": {
                    "type": "boolean",
                    "description": "Set true only after the user has verbally confirmed 'add anyway' despite a similar task existing.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "type": "function",
        "name": "show_task_creation",
        "description": (
            "Show the task-creation confirmation screen on the device. Call this — instead of "
            "create_task — whenever the user directly asks to create, add, or save a task/reminder. "
            "The user reviews the pre-filled title and date on screen, then confirms (saves) or "
            "discards (cancels) — EITHER by speaking ('confirm' / 'discard' / 'yes save it' / "
            "'no cancel') OR by tapping the on-screen buttons. "
            "DATE RULES: "
            "(1) If the user mentioned a date ('tomorrow', 'Sunday', 'by the 15th'), resolve it to "
            "YYYY-MM-DD and pass as due_date. "
            "(2) If NO date was mentioned, ask exactly once: "
            "'When would you like this task due? Or say no date to keep it unplanned.' "
            "Wait for the reply, resolve the date if given, then call this tool — passing due_date "
            "only if explicitly provided. Omitting due_date places the task in Unplanned. "
            "TITLE RULE: ≤8-word paraphrase of what the user said — keep the verb and object. "
            "After calling this tool, say exactly: 'I've set it up — say confirm to save it, "
            "say discard to cancel, or tap the buttons on screen.' Then WAIT. The task is NOT "
            "saved yet. When the user confirms, call confirm_task_creation. When they cancel, call "
            "discard_task_creation. Do NOT call create_task for direct user requests."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Task title — ≤8-word paraphrase of what the user said "
                        "(e.g. 'Buy groceries', 'Call John about proposal')."
                    ),
                },
                "due_date": {
                    "type": "string",
                    "description": (
                        "Optional ISO date YYYY-MM-DD. Set ONLY when the user explicitly mentioned "
                        "a date. Resolve 'tomorrow', 'Sunday', 'next Monday' to a real date. "
                        "Omit if no date was mentioned — the task goes to Unplanned."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Optional detail the user explicitly spoke. Never invent."
                    ),
                },
            },
            "required": ["title"],
        },
    },
    {
        "type": "function",
        "name": "confirm_task_creation",
        "description": (
            "Commit (save) the task that is currently shown on the task-creation screen. "
            "Call this the moment the user verbally confirms after show_task_creation — e.g. "
            "'confirm', 'yes', 'save it', 'go ahead', 'do it'. This actually writes the task to "
            "the user's list and dismisses the screen on the device. Pass the SAME title, due_date "
            "and description you used in show_task_creation (re-state them exactly — do not change "
            "the wording or invent a date). After it returns success, confirm: 'Done — it's on "
            "your list.' If it returns an error, apologise and offer to try again."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Same task title shown on screen (≤8-word paraphrase).",
                },
                "due_date": {
                    "type": "string",
                    "description": (
                        "Same ISO date YYYY-MM-DD shown on screen, or omit if the task is Unplanned."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "Same optional detail shown on screen, if any.",
                },
                "confirmed_by_user": {
                    "type": "boolean",
                    "description": (
                        "Must be true, and only after the user has explicitly approved saving this "
                        "task (a spoken yes / 'save it' / 'go ahead', or a Confirm tap)."
                    ),
                },
                "confirmation_phrase": {
                    "type": "string",
                    "description": (
                        "The user's actual approving words (e.g. 'yes save it', 'go ahead') or the "
                        "'[BUTTON:Confirm]' marker the device sends on a Confirm tap. The server "
                        "validates this is genuine approval before writing."
                    ),
                },
            },
            "required": ["title", "confirmed_by_user", "confirmation_phrase"],
        },
    },
    {
        "type": "function",
        "name": "discard_task_creation",
        "description": (
            "Cancel the task that is currently shown on the task-creation screen WITHOUT saving it. "
            "Call this when the user verbally declines after show_task_creation — e.g. 'discard', "
            "'cancel', 'no', 'never mind', 'forget it'. This dismisses the screen on the device. "
            "After it returns, acknowledge briefly: 'Okay, cancelled.'"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "show_calendar_event",
        "description": (
            "Show the calendar event-creation screen on the device. Call this — instead of "
            "assistant_intent — whenever the user directly asks to schedule, create, set up, or add "
            "a calendar event / meeting / appointment. The screen has five fields: Event Name, Date, "
            "Time, Duration, and Attendees. The user reviews them on screen, then confirms (creates) or "
            "discards (cancels) — EITHER by speaking ('confirm' / 'discard' / 'yes create it' / "
            "'no cancel') OR by tapping the on-screen buttons. "
            "CALL IT PROGRESSIVELY: open it as soon as the event flow begins (with whatever fields "
            "you have — they MAY be empty) and call it again as you collect each field so the screen "
            "fills in live. Only the fields you pass are updated; omitted fields keep their value. "
            "DATE: resolve relative dates ('tomorrow', 'next Monday') to YYYY-MM-DD. "
            "TIME: pass a 24-hour HH:MM string (e.g. '15:30'). "
            "DURATION: pass duration_minutes as an integer (e.g. 30, 45, 60). "
            "ATTENDEES: add a person ONLY after resolving them with show_recipient_picker "
            "(field='attendee') and the user confirms — the confirmed contact appears as a chip on "
            "this screen automatically, so you usually do NOT need to re-pass attendees here. "
            "After the screen has the details, say exactly: 'I've set it up — say confirm to create "
            "it, say discard to cancel, or tap the buttons on screen.' Then WAIT. The event is NOT "
            "created yet. IMPORTANT: this review stage is still fully editable — if the user asks to "
            "add/remove/replace attendees, or change name/date/time/duration, apply that update "
            "immediately via show_calendar_event and/or show_recipient_picker(field='attendee') "
            "instead of refusing or forcing confirm/discard first. When the user confirms, call "
            "confirm_calendar_event. When they cancel, call discard_calendar_event."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Event name / title (e.g. 'Marketing review').",
                },
                "date": {
                    "type": "string",
                    "description": "Event date as ISO YYYY-MM-DD. Resolve relative dates yourself.",
                },
                "time": {
                    "type": "string",
                    "description": "Event start time as 24-hour HH:MM (e.g. '15:30').",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Event duration in minutes (e.g. 30, 45, 60).",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Confirmed attendees as email addresses (optionally 'Name <email>'). "
                        "Usually left empty — attendees confirmed via show_recipient_picker "
                        "(field='attendee') are added to the screen automatically."
                    ),
                },
                "attendees_mode": {
                    "type": "string",
                    "enum": ["replace", "append"],
                    "description": "How attendee list should be applied. Default 'replace'.",
                },
                "edit_mode": {
                    "type": "boolean",
                    "description": (
                        "True when opening an existing event for edits even if event_id is unknown."
                    ),
                },
                "event_id": {
                    "type": "string",
                    "description": (
                        "Existing event id when editing a previously created calendar event. "
                        "If omitted, this is a fresh invite workflow."
                    ),
                },
                "reset": {
                    "type": "boolean",
                    "description": (
                        "When true (default for fresh invites), clear prior draft state before showing."
                    ),
                },
            },
        },
    },
    {
        "type": "function",
        "name": "confirm_calendar_event",
        "description": (
            "Create — or, when event_id is provided, UPDATE — the calendar event currently shown on "
            "the calendar-event screen. Call this the moment the user confirms after "
            "show_calendar_event — e.g. 'confirm', 'yes', 'create it', 'go ahead', 'add it'. This "
            "writes the event to the user's Google Calendar and dismisses the screen on the device. "
            "Pass the SAME name, date, time and attendees shown on screen (re-state them exactly — do "
            "not change wording or invent details). FOR AN EDIT, also pass event_id so the existing "
            "event is updated in place instead of a new one being created; if you don't have the "
            "event_id, the original name + date are used to locate it. After it returns success the "
            "device sends the user to the Calendar screen; confirm: 'Done — it's on your calendar. "
            "You can see it there now.' If it returns an error, apologise and offer to try again."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Same event name shown on screen.",
                },
                "date": {
                    "type": "string",
                    "description": "Same event date (ISO YYYY-MM-DD) shown on screen.",
                },
                "time": {
                    "type": "string",
                    "description": "Same event start time (24-hour HH:MM) shown on screen.",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Event duration in minutes. Default 30 if not specified.",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Same attendee email addresses shown on screen, if any.",
                },
                "event_id": {
                    "type": "string",
                    "description": (
                        "Google Calendar event id of an EXISTING event being edited. Pass this ONLY "
                        "when updating an event the user already had (rename / reschedule / add "
                        "attendee). Leave empty to create a brand-new event."
                    ),
                },
                "attendees_add": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Attendees to add during edit mode.",
                },
                "attendees_remove": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Attendees to remove during edit mode.",
                },
                "attendees_replace": {
                    "type": "boolean",
                    "description": (
                        "When true in edit mode, replace attendee list with provided attendees."
                    ),
                },
                "edit_mode": {
                    "type": "boolean",
                    "description": "Force update mode even if event_id is unavailable.",
                },
                "confirmed_by_user": {
                    "type": "boolean",
                    "description": (
                        "Must be true, and only after the user has explicitly approved creating/"
                        "updating this event (a spoken yes / 'create it' / 'go ahead', or a Confirm tap)."
                    ),
                },
                "confirmation_phrase": {
                    "type": "string",
                    "description": (
                        "The user's actual approving words (e.g. 'yes create it', 'go ahead') or the "
                        "'[BUTTON:Confirm]' marker the device sends on a Confirm tap. The server "
                        "validates this is genuine approval before writing."
                    ),
                },
            },
            "required": ["name", "date", "time", "confirmed_by_user", "confirmation_phrase"],
        },
    },
    {
        "type": "function",
        "name": "discard_calendar_event",
        "description": (
            "Cancel the event currently shown on the calendar-event screen WITHOUT creating it. "
            "Call this when the user verbally declines after show_calendar_event — e.g. 'discard', "
            "'cancel', 'no', 'never mind', 'forget it'. This dismisses the screen on the device. "
            "After it returns, acknowledge briefly: 'Okay, cancelled.'"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "list_tasks",
        "description": (
            "Read the user's tasks. Use when they ask 'show my tasks', 'what's on my list', "
            "'any tasks today', 'unplanned tasks', 'pending tasks', etc. Returns title, id, due_at, "
            "status, detail, source for each task. Filter by status when relevant."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "snoozed", "completed", "cancelled", "all"],
                    "description": "Status filter. Default: open (active + snoozed). Use 'all' for full history.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max tasks to return (1-100). Default 30.",
                },
            },
        },
    },
    {
        "type": "function",
        "name": "update_task",
        "description": (
            "Update an existing task: mark completed, cancel/delete, snooze, or set/change a due date. "
            "Use when the user says 'mark X done', 'cancel that task', 'snooze X for tomorrow', "
            "'set X to Friday', or 'I finished X'. You must identify the task either by task_id "
            "(preferred — get it from list_tasks first) or by title_match (a few words from the title). "
            "Title-match resolves to the closest active task; if multiple match, returns the candidates "
            "and asks the agent to disambiguate."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Exact task id from list_tasks (preferred way to identify).",
                },
                "title_match": {
                    "type": "string",
                    "description": "Fallback: a few words from the task title to match fuzzily (e.g. 'call John').",
                },
                "status": {
                    "type": "string",
                    "enum": ["completed", "cancelled", "snoozed", "active"],
                    "description": "New status. 'completed' for done, 'cancelled' for delete, 'snoozed' to defer, 'active' to un-snooze.",
                },
                "due_date": {
                    "type": "string",
                    "description": "Optional ISO date YYYY-MM-DD to set as the new due_at (use this for 'set X to Friday' or 'change deadline').",
                },
            },
        },
    },
    {
        "type": "function",
        "name": "extract_tasks_from_emails",
        "description": (
            "Scan the user's recent emails for action items where the user is the named actionee, and "
            "return them as PROPOSED tasks (not saved). The agent must read each proposal aloud and "
            "wait for verbal confirmation before calling create_task for each accepted one. "
            "Use ONLY when the user explicitly says 'any tasks in my inbox?', 'turn that email into "
            "a task', 'extract tasks from emails', or similar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional Gmail search query to narrow the scan (e.g. 'from:boss', 'subject:project'). Empty = recent inbox.",
                },
                "max_emails": {
                    "type": "integer",
                    "description": "How many recent emails to scan (1-15). Default 5.",
                },
            },
        },
    },
    {
        "type": "function",
        "name": "lookup_email_contacts",
        "description": (
            "Look up known email addresses for a person by name or partial email. "
            "Call this BEFORE drafting or sending any email when the user gives only a name "
            "(e.g. 'email vivek', 'draft mail to priya'). Returns up to 5 matching contacts "
            "sorted by how often the user has interacted with them. "
            "If matches are found, read them out and ask: "
            "'I have these addresses for [name] — (1) email1 (2) email2. Is it one of these, or a different address?' "
            "If no matches are found, ask the user to spell out the address."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The person's name or partial email to search (e.g. 'vivek', 'priya', 'gmail.com').",
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "show_recipient_picker",
        "description": (
            "RESOLVE AND CONFIRM a person the user referred to by NAME. This is the REQUIRED first "
            "step whenever the user wants to email / draft / reply / forward to a person — OR add a "
            "person as an attendee to a calendar invite — without spelling the full address (e.g. "
            "'email Rahul', 'draft a mail to Neha', 'add Priya to the invite', 'invite Karthik'). "
            "Set field='attendee' when the person is being added to a calendar event so their "
            "resolved address lands on the invite. "
            "The server searches ALL known contact sources (sent mail, received mail, draft "
            "recipients, calendar attendees) ranked by interaction frequency, and displays the "
            "matching contacts as tappable cards on the device screen so the user can confirm by "
            "voice OR touch. "
            "CRITICAL: you MUST call this and wait for the user to confirm BEFORE drafting / adding — "
            "never assume a recipient, even when only one match exists. "
            "Behavior based on the returned 'count': "
            "1 match -> say e.g. 'I found [Name] at [email] — is that the right person?' and wait for "
            "a yes / tap. "
            ">1 match -> say e.g. 'I found a few: (1) [Name] [email], (2) [Name] [email]. Which one?' "
            "and wait for a spoken choice ('the first one' / a name) or a tap. "
            "0 matches -> NO picker is shown; say EXACTLY 'There are no contacts associated with "
            "[name]. Please provide the email address.', take the dictated address, then call "
            "remember_contact. "
            "ONCE THE USER CONFIRMS a contact (by voice or by tapping a card), the picker is "
            "dismissed automatically — do NOT call show_recipient_picker again for that same "
            "person. Proceed straight to show_email_draft (for emails) or show_calendar_event (for "
            "attendees) to record the choice. Re-calling the picker re-opens the popup and traps the "
            "user. "
            "For 'email Rahul and Neha', call show_recipient_picker once per person and confirm each "
            "before continuing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The person's name (or partial name/email) the user said, e.g. 'Rahul', 'Neha Sharma'.",
                },
                "field": {
                    "type": "string",
                    "enum": ["to", "cc", "bcc", "attendee"],
                    "description": (
                        "Which field this contact is being resolved for. "
                        "Use 'to' for direct email recipients, 'cc' for CC, 'bcc' for BCC, and "
                        "'attendee' when adding a person to a calendar event being created on the "
                        "calendar-event screen. Defaults to 'to' if omitted."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "remember_contact",
        "description": (
            "Validate and save a NEW email contact for future use. Call this after the user dictates "
            "an email address for a person you could not find with show_recipient_picker, so the "
            "address is remembered next time. The server validates the address format and stores it "
            "in the user's known contacts. If the address is invalid it returns an error — re-ask the "
            "user to repeat it. Always read the address back letter-by-letter for confirmation before "
            "using it in a draft."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The person's display name as the user referred to them (e.g. 'Rahul', 'Neha Sharma').",
                },
                "email": {
                    "type": "string",
                    "description": "The full email address the user dictated (e.g. 'rahul@company.com').",
                },
            },
            "required": ["email"],
        },
    },
    {
        "type": "function",
        "name": "show_email_draft",
        "description": (
            "Display or update the email draft popup on the device screen. OPEN IT IMMEDIATELY: the very "
            "first time an email task begins (user asks to draft / write / send / reply / forward), call "
            "this with state='drafting' and whatever fields you have (they MAY be empty) BEFORE you "
            "resolve recipients or ask for context — this navigates the device to the draft page so the "
            "recipient picker and your questions appear on that page. This popup is the PRIMARY "
            "review surface for emails — the user reads the draft here, you do NOT read the full body "
            "aloud. Call it as you compose so fields appear progressively (recipient first, then "
            "subject, then body) and again after any edit so the screen always matches the live draft. "
            "Keep your spoken reply SHORT (e.g. 'I've drafted the email.', 'The draft is ready for "
            "review.', 'Updated.'). "
            "Pass only confirmed recipients. Leave cc / bcc empty unless the user asked for them — "
            "empty cc/bcc rows are hidden and their space is given to the body. "
            "Set 'state' to reflect the lifecycle: 'drafting' while composing/editing, 'ready' once "
            "the draft is complete and awaiting the user's decision, 'sent' after an approved send, "
            "'saved' after saving to Gmail drafts, 'discarded' if the user discards it. "
            "Never use this tool to send — sending still requires the normal approval flow."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["drafting", "ready", "sending", "sent", "saved", "discarded"],
                    "description": "Lifecycle state of the draft. Default 'drafting'.",
                },
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Confirmed To recipients (email addresses, optionally 'Name <email>').",
                },
                "cc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Cc recipients. Omit or leave empty unless the user asked for cc.",
                },
                "bcc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Bcc recipients. Omit or leave empty unless the user asked for bcc.",
                },
                "subject": {
                    "type": "string",
                    "description": "The email subject line.",
                },
                "body": {
                    "type": "string",
                    "description": "The full email body text as composed so far.",
                },
                "draft_id": {
                    "type": "string",
                    "description": "Optional Gmail draft id once a draft has been created/updated.",
                },
                "reply_all_thread_id": {
                    "type": "string",
                    "description": (
                        "Set this to the thread id when showing a REPLY-ALL draft. The server "
                        "fills the popup's To + complete Cc list with every thread participant "
                        "(minus the user) so the screen shows exactly who the reply will reach. "
                        "Do NOT list the cc addresses yourself — just pass the thread id. Omit "
                        "for new emails and normal single-recipient replies."
                    ),
                },
            },
        },
    },
    # ── Email view tools ─────────────────────────────────────────────────────
    {
        "type": "function",
        "name": "fetch_and_show_email",
        "description": (
            "Search Gmail for a specific email and display its full content on the device screen. "
            "This is the PRIMARY tool whenever the user asks to open, view, read, or see an email "
            "(e.g. 'show me the email from Shiva', 'open the latest email', 'show unread emails', "
            "'the email about the progress update'). One call does everything — the server searches "
            "Gmail, fetches the full body, and populates the screen automatically. "
            "You do NOT need to call show_email_view or assistant_intent separately. "
            "After the call, say something short like 'Here\\'s that email from [sender].' "
            "and do NOT read the body aloud unless the user explicitly asks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Gmail search string to find the email. Use Gmail q syntax. Examples: "
                        "'from:Shiva' — emails from Shiva; "
                        "'is:unread' — latest unread email; "
                        "'subject:progress update' — by subject keyword; "
                        "'from:Vivek is:unread' — unread from Vivek. "
                        "Leave empty or omit to fetch the most recent inbox email."
                    ),
                },
            },
        },
    },
    {
        "type": "function",
        "name": "show_email_view",
        "description": (
            "Display a single received email on the device screen. Call this IMMEDIATELY once "
            "you have retrieved the email content — the user has asked to SEE a specific email "
            "on the screen (not compose one). The device navigates to the email view page and "
            "renders the email in a large, readable card. "
            "Always call this before reading the email aloud — let the screen be the primary "
            "surface and keep your spoken summary SHORT (e.g. 'Here's the email from Shiva.'). "
            "You do NOT need to read the full body unless the user asks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "The email subject line.",
                },
                "sender_name": {
                    "type": "string",
                    "description": "Display name of the sender (e.g. 'Shiva Kumar').",
                },
                "sender_initial": {
                    "type": "string",
                    "description": (
                        "Single letter to show in the sender avatar circle "
                        "(first letter of sender_name). Omit to auto-derive."
                    ),
                },
                "sender_email": {
                    "type": "string",
                    "description": "Sender's email address (optional, not displayed prominently).",
                },
                "time": {
                    "type": "string",
                    "description": "Human-readable time/date of the email (e.g. '2:57 PM', 'Mon 9 AM').",
                },
                "recipient_label": {
                    "type": "string",
                    "description": (
                        "Short label for the recipient field shown below the sender name. "
                        "Use 'to me' for emails sent directly to the user, or a short name "
                        "like 'to Vivek' when appropriate. Defaults to 'to me'."
                    ),
                },
                "body": {
                    "type": "string",
                    "description": "Full plain-text body of the email.",
                },
            },
            "required": ["subject", "body"],
        },
    },
    # ── Personal Notes tools ──────────────────────────────────────────────────
    {
        "type": "function",
        "name": "note_create",
        "description": (
            "Save a personal note for the user. Call this when they say 'take a note', "
            "'note this down', 'remember this for me', 'jot that down', 'save this idea', etc. "
            "The note is stored permanently and can be recalled in future sessions via memory_search "
            "or note_list. Always confirm after saving: 'I've saved that note for you.'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short descriptive title (1-8 words). If not obvious from context, infer from content.",
                },
                "content": {
                    "type": "string",
                    "description": "Full note content as the user dictated or implied.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional topic tags (e.g. ['work', 'ideas', 'follow-up']).",
                },
                "pinned": {
                    "type": "boolean",
                    "description": "True if the user explicitly asks to pin the note.",
                },
            },
        },
    },
    {
        "type": "function",
        "name": "note_list",
        "description": (
            "List or BROWSE the user's saved notes (e.g. 'show my notes', 'read my notes'). "
            "Returns notes newest-first with their created date/time. Supports keyword, tag, "
            "and date-range filters. Each note includes created_at — when reading a note back, "
            "tell the user WHEN it was saved. "
            "NOTE: for finding ONE specific note by topic/person/context (e.g. 'pull up my note "
            "about the board meeting'), prefer assistant_intent, which ranks by meaning, not just "
            "title text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Keyword(s) to search in title and content. A note matches if ANY keyword appears — pass a natural phrase, not an exact title.",
                },
                "tag": {
                    "type": "string",
                    "description": "Optional tag to filter by (e.g. 'work').",
                },
                "pinned_only": {
                    "type": "boolean",
                    "description": "If true, return only pinned notes.",
                },
                "date_from": {
                    "type": "string",
                    "description": "Only notes created on/after this date (YYYY-MM-DD). Use for 'notes from June 17', 'notes since Monday', etc.",
                },
                "date_to": {
                    "type": "string",
                    "description": "Only notes created on/before this date (YYYY-MM-DD). For a single day, set date_from and date_to to the same date.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max notes to return (default 10, max 50).",
                },
            },
        },
    },
    {
        "type": "function",
        "name": "note_update",
        "description": (
            "Update an existing note. Use when the user wants to edit, rename, add to, or "
            "pin/unpin a note. You must have a note_id from a prior note_list call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "The note id to update (from note_list).",
                },
                "title": {
                    "type": "string",
                    "description": "New title. Omit to keep existing.",
                },
                "content": {
                    "type": "string",
                    "description": "New content. Omit to keep existing.",
                },
                "append": {
                    "type": "boolean",
                    "description": "If true, append content to the existing text instead of replacing it.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replace the tags list. Omit to keep existing.",
                },
                "pinned": {
                    "type": "boolean",
                    "description": "Set pinned status.",
                },
            },
            "required": ["note_id"],
        },
    },
    {
        "type": "function",
        "name": "note_delete",
        "description": (
            "Permanently delete a note. Only call when the user explicitly confirms deletion. "
            "Always confirm after: 'Done, I've deleted that note.'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "The note id to delete (from note_list).",
                },
            },
            "required": ["note_id"],
        },
    },
]

# Live-internet tools are intentionally disabled: this is an audio-only personal
# assistant whose scope is the user's own meetings, calendar, emails, tasks, notes
# and saved memory (plus general knowledge it already has). It does NOT browse the
# web or fetch real-time data. The definitions above are kept for easy re-enable,
# but they are never exposed to the model and are refused if somehow invoked.
_DISABLED_VOICE_TOOLS = frozenset(
    {
        "web_search",
        "deep_research",
        "get_news",
        "get_weather",
        "get_stock_price",
        "convert_currency",
        "get_sports_score",
        "find_research_paper",
    }
)

REALTIME_VOICE_TOOL_DEFINITIONS = [
    _t for _t in REALTIME_VOICE_TOOL_DEFINITIONS if _t.get("name") not in _DISABLED_VOICE_TOOLS
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


import email.utils as _email_utils
import re as _re

_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_email_address(raw: str) -> tuple[str, str]:
    """Parse 'Name <addr>' or a bare address. Returns (name, lowercased_email) or ('', '')."""
    try:
        name, addr = _email_utils.parseaddr(raw or "")
        addr = (addr or "").strip().lower()
        if addr and _EMAIL_RE.match(addr):
            return name.strip(), addr
    except Exception:
        pass
    return "", ""


def _normalize_recipient_list(value: Any) -> list[dict[str, str]]:
    """Normalize a To/Cc/Bcc value into a list of {name, email} dicts.

    Accepts a list of strings ('addr' or 'Name <addr>'), a comma-separated
    string, or a list of {name,email} dicts. Invalid entries are dropped.
    """
    items: list[Any] = []
    if value is None:
        return []
    if isinstance(value, str):
        items = [p for p in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]

    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            addr = str(item.get("email") or "").strip()
            disp = str(item.get("name") or "").strip()
            pname, paddr = _parse_email_address(addr)
            if paddr:
                out.append({"name": disp or pname, "email": paddr})
            elif addr:
                # Keep an as-yet-unvalidated address so the UI can still show it.
                out.append({"name": disp, "email": addr.strip().lower()})
        else:
            raw = str(item or "").strip()
            if not raw:
                continue
            pname, paddr = _parse_email_address(raw)
            if paddr:
                out.append({"name": pname, "email": paddr})
            else:
                out.append({"name": "", "email": raw.lower()})
    return out


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

    if name in _DISABLED_VOICE_TOOLS:
        # Live-web tools are disabled; never run a lookup even if a stale client requests one.
        return json.dumps(
            {
                "disabled": True,
                "scope": "personal_only",
                "message": (
                    "Live web and real-time lookups are turned off. This assistant only covers the "
                    "user's own meetings, calendar, emails, tasks, notes and saved memory, plus "
                    "general knowledge it already has. Tell the user warmly that this is outside "
                    "your scope and offer to help with their personal info instead. Do NOT mention "
                    "tools, and never ask them to type, paste, or show you anything."
                ),
            }
        )

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
            raw_date = (args.get("date") or "").strip() or None
            # Serve the session-warmed cache only for the canonical default
            # briefing (2-day window, no specific date) so the first call in a
            # voice session is near-instant. Any other shape is built fresh.
            bundle = None
            if raw_date is None and da == 2:
                from services.briefing_context import get_cached_briefing
                bundle = get_cached_briefing(user_id)
            if bundle is None:
                bundle = build_briefing_context_dict(
                    actor=actor,
                    user_id=user_id,
                    days_ahead=da,
                    date=raw_date,
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

        if name == "get_sent_emails":
            query = str(args.get("query") or "").strip()
            max_r = min(max(int(args.get("max_results") or 5), 1), 20)
            try:
                from routes.integrations import get_credentials_for_provider
                from services.gmail import list_recent_messages
                creds_g = get_credentials_for_provider(user_id, "gmail")
                if not creds_g:
                    return json.dumps({"error": "Gmail not connected. Ask the user to connect Gmail in Settings."})
                q = f"in:sent {query}".strip()
                msgs = list_recent_messages(creds_g, max_results=max_r, q=q)
                # Annotate each message for clarity when speaking to the user.
                # Each msg now has: to, cc (if present), from (user's own address), subject, date, snippet.
                for m in msgs:
                    m["direction"] = "sent"
                try:
                    maybe_ingest_gmail_snapshot(user_id, {"messages": msgs, "count": len(msgs)})
                except Exception:
                    logger.debug("get_sent_emails: mem0 ingest failed", exc_info=True)
                return json.dumps({
                    "sent_messages": msgs,
                    "count": len(msgs),
                    "note": (
                        "These are sent emails. Each entry includes 'to' (primary recipient), "
                        "'cc' (if any), 'subject', 'date', and 'snippet'. "
                        "Read the 'to' field to answer who the user emailed."
                    ),
                }, default=str)
            except Exception as exc:
                logger.warning("get_sent_emails failed: %s", exc)
                return json.dumps({"error": str(exc)[:300]})

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
            ok_appr, appr_err = require_user_approval(
                args.get("confirmed_by_user"), args.get("confirmation_phrase")
            )
            if not ok_appr:
                return json.dumps(appr_err)
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
                result = out.get("result") if isinstance(out.get("result"), dict) else {}
                email_result_keys = (
                    "gmail",
                    "gmail_send_draft",
                    "gmail_reply",
                    "gmail_reply_all",
                    "gmail_forward",
                )
                if ok and any(k in result for k in email_result_keys):
                    # The email send itself is the committed write. Do not rely on
                    # the model to remember a follow-up show_email_draft(state=sent)
                    # call; emit the device terminal directive from the write result
                    # so the send animation always fires exactly when the send
                    # actually succeeds.
                    out["device_email_draft"] = {"state": "sent"}
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
            if raw in ("task", "action_items", "todo", "todos"):
                raw = "tasks"
            if raw in (
                "transcription", "audio_transcription", "transcribe", "transcript",
                "listening", "voice", "voice_assistant", "recording", "assistant",
            ):
                raw = "voice_session"
            if raw not in REALTIME_DEVICE_NAV_SCREENS:
                return json.dumps(
                    {
                        "error": "invalid_screen",
                        "allowed": sorted(REALTIME_DEVICE_NAV_SCREENS),
                    }
                )
            payload: dict = {"ok": True, "device_navigate": raw}
            if raw == "calendar":
                td = str(args.get("target_date") or "").strip()
                if td:
                    payload["target_date"] = td
            if raw == "tasks":
                tab = str(args.get("target_tab") or "").strip().lower()
                _task_tab_aliases = {
                    "today": "due_today", "due_today": "due_today",
                    "upcoming": "upcoming",
                    "unfinished": "overdue", "overdue": "overdue", "past_due": "overdue",
                    "unplanned": "unplanned", "no_date": "unplanned",
                }
                tab = _task_tab_aliases.get(tab, "")
                if tab:
                    payload["target_tab"] = tab
            if raw == "emails":
                tab = str(args.get("target_tab") or "").strip().lower()
                _email_tab_aliases = {
                    "today": "today",
                    "all": "all", "all mail": "all", "everything": "all",
                    "unread": "unread", "new": "unread",
                    "sent": "sent", "sent mail": "sent", "outbox": "sent",
                    "drafts": "drafts", "draft": "drafts",
                }
                tab = _email_tab_aliases.get(tab, "")
                if tab:
                    payload["target_tab"] = tab
            if raw == "morning_brief":
                tab = str(args.get("target_tab") or "").strip().lower()
                _mb_tab_aliases = {
                    "schedule": "schedule", "calendar": "schedule",
                    "meetings": "schedule", "meeting": "schedule", "today": "schedule",
                    "tasks": "tasks", "task": "tasks", "todo": "tasks", "todos": "tasks",
                    "emails": "emails", "email": "emails", "inbox": "emails", "mail": "emails",
                    "next": "next", "forward": "next",
                    "previous": "previous", "prev": "previous", "back": "previous",
                }
                tab = _mb_tab_aliases.get(tab, "")
                if tab:
                    payload["target_tab"] = tab
                _mb_steps = {
                    "schedule": (
                        "The morning-brief carousel is now on the SCHEDULE card. Speak ONLY today's "
                        "meetings now — lead with the next upcoming meeting (time + title), then the rest, "
                        "in one or two short sentences. Do NOT mention tasks or emails yet. The MOMENT you "
                        "finish speaking the meetings aloud, call "
                        "navigate_device_ui(screen='morning_brief', target_tab='tasks')."
                    ),
                    "tasks": (
                        "The carousel is now on the TASKS card. Speak ONLY tasks due today now. Do NOT "
                        "mention overdue, upcoming, or unplanned tasks. Do NOT mention emails yet. The "
                        "MOMENT you finish, call navigate_device_ui(screen='morning_brief', target_tab='emails')."
                    ),
                    "emails": (
                        "The carousel is now on the EMAILS card. Speak ONLY the latest unread emails now "
                        "(sender + subject), briefly. This is the FINAL section — after it, give a one-line "
                        "wrap-up. Do NOT call navigate_device_ui again."
                    ),
                    "next": "The carousel advanced to the next card. Narrate the section now shown, briefly.",
                    "previous": "The carousel moved to the previous card. Narrate the section now shown, briefly.",
                }
                if tab in _mb_steps:
                    payload["briefing_step"] = _mb_steps[tab]
            return json.dumps(payload)

        if name == "show_meeting_summary":
            mid = str(args.get("meeting_id") or "").strip()
            query = str(args.get("query") or "").strip()
            stype = str(args.get("session_type") or "").strip().lower()
            if stype not in ("meeting", "note"):
                stype = None
            top: dict[str, Any] | None = None
            if not mid:
                if not query:
                    return json.dumps({"error": "query_required"})
                try:
                    res = memory_search_meetings(
                        user_id, query, max_results=8, session_type=stype
                    )
                except Exception as e:
                    logger.exception("show_meeting_summary search failed")
                    return json.dumps({"error": "search_failed", "detail": str(e)})
                if res.get("needs_clarification") and res.get("clarification"):
                    return json.dumps(
                        {
                            "ok": True,
                            "needs_clarification": True,
                            "clarification": res.get("clarification"),
                        },
                        default=str,
                    )
                meetings = res.get("meetings") or []
                if not meetings:
                    return json.dumps(
                        {
                            "ok": True,
                            "found": False,
                            "message": "No matching meeting or note recording was found.",
                        }
                    )
                top = meetings[0]
                mid = str(top.get("id") or "").strip()
            if not mid:
                return json.dumps(
                    {
                        "ok": True,
                        "found": False,
                        "message": "No matching meeting or note recording was found.",
                    }
                )

            # Resolve the summary content server-side so the device paints it
            # directly (mirrors tapping the "summary ready" notification, which
            # passes the real summary dict). This avoids relying solely on the
            # device's secondary fetch.
            def _fetch_detail(rec_id: str) -> dict[str, Any] | None:
                try:
                    d = memory_fetch_meeting(
                        user_id, rec_id, max_segments=1, max_total_chars=600
                    )
                    return d if isinstance(d, dict) and not d.get("error") else None
                except Exception:
                    logger.debug("show_meeting_summary detail fetch failed", exc_info=True)
                    return None

            detail = _fetch_detail(mid)
            # If the top hit has no summary yet, prefer the next ranked recording
            # that actually has one (only when we searched — not for an explicit id).
            if (not detail or not str(detail.get("summary") or "").strip()) and top is not None:
                for cand in (meetings or [])[1:5]:
                    cid = str(cand.get("id") or "").strip()
                    if not cid or cid == mid:
                        continue
                    d2 = _fetch_detail(cid)
                    if d2 and str(d2.get("summary") or "").strip():
                        detail, mid, top = d2, cid, cand
                        break

            summary_data: dict[str, Any] = {}
            if detail:
                summary_data = {
                    "title": detail.get("title"),
                    "recording_mode": detail.get("recording_mode") or detail.get("session_type"),
                    "started_at": detail.get("start_time") or detail.get("created_at"),
                    "generated_at": detail.get("created_at"),
                    "participants": detail.get("participants") or [],
                    "summary": {
                        "summary": detail.get("summary") or "",
                        "action_items": detail.get("action_items") or [],
                        "decisions": detail.get("decisions") or [],
                    },
                }

            payload: dict[str, Any] = {
                "ok": True,
                "found": True,
                "device_navigate": "summary_review",
                "meeting_id": mid,
                "summary_data": summary_data,
            }
            src = detail if detail else (top if isinstance(top, dict) else None)
            if isinstance(src, dict):
                payload["title"] = src.get("title")
                payload["recorded_at"] = src.get("start_time") or src.get("created_at")
                payload["session_type"] = src.get("session_type") or src.get("recording_mode")
            return json.dumps(payload, default=str)

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

        if name == "convert_currency":
            from services.research import fetch_currency_convert_sync
            try:
                amount = float(args.get("amount") if args.get("amount") not in (None, "") else 1.0)
            except (TypeError, ValueError):
                amount = 1.0
            frm = str(args.get("from") or args.get("from_currency") or "").strip()
            to = str(args.get("to") or args.get("to_currency") or "").strip()
            if not frm or not to:
                return json.dumps({"error": "missing_currency", "detail": "Both 'from' and 'to' are required."})
            try:
                result = fetch_currency_convert_sync(amount=amount, from_ccy=frm, to_ccy=to)
            except Exception as exc:
                logger.warning("convert_currency failed: %s", exc)
                return json.dumps({"error": "currency_unavailable", "detail": str(exc)})
            return json.dumps(result, default=str)

        if name == "get_stock_price":
            from services.research import fetch_stock_price_sync
            ticker = str(args.get("ticker") or args.get("symbol") or "").strip()
            if not ticker:
                return json.dumps({"error": "ticker_required"})
            try:
                result = fetch_stock_price_sync(ticker)
            except Exception as exc:
                logger.warning("get_stock_price failed: %s", exc)
                return json.dumps({"error": "stock_unavailable", "ticker": ticker, "detail": str(exc)})
            return json.dumps(result, default=str)

        if name == "get_sports_score":
            from services.research import fetch_sports_score_sync
            q = str(args.get("query") or args.get("match") or "").strip()
            if not q:
                return json.dumps({"error": "query_required"})
            try:
                result = fetch_sports_score_sync(q)
            except Exception as exc:
                logger.warning("get_sports_score failed: %s", exc)
                return json.dumps({"error": "sports_unavailable", "query": q, "detail": str(exc)})
            return json.dumps(result, default=str)

        if name == "find_research_paper":
            from services.research import fetch_research_paper_sync
            q = str(args.get("query") or args.get("topic") or "").strip()
            if not q:
                return json.dumps({"error": "query_required"})
            try:
                limit = int(args.get("limit") or 5)
            except (TypeError, ValueError):
                limit = 5
            try:
                result = fetch_research_paper_sync(query=q, limit=limit)
            except Exception as exc:
                logger.warning("find_research_paper failed: %s", exc)
                return json.dumps({"error": "paper_search_unavailable", "query": q, "detail": str(exc)})
            return json.dumps(result, default=str)

        if name == "deep_research":
            from services.research import fetch_deep_research_sync
            topic = str(args.get("topic") or args.get("query") or "").strip()
            if not topic:
                return json.dumps({"error": "topic_required"})
            depth = str(args.get("depth") or "shallow").strip().lower()
            if depth not in ("shallow", "medium", "deep"):
                depth = "shallow"
            try:
                result = fetch_deep_research_sync(topic=topic, depth=depth, original_message=topic)
            except Exception as exc:
                logger.warning("deep_research failed: %s", exc)
                return json.dumps({"error": "deep_research_unavailable", "topic": topic, "detail": str(exc)})
            return json.dumps(result, default=str)

        if name == "show_task_creation":
            title = str(args.get("title") or "").strip()
            if not title:
                return json.dumps({"ok": False, "error": "title_required"})
            due_date    = str(args.get("due_date")    or "").strip() or None
            description = str(args.get("description") or "").strip() or None
            return json.dumps({
                "ok": True,
                "device_task_creation": {
                    "title":       title,
                    "description": description,
                    "due_date":    due_date,
                },
                "message": (
                    "Task creation UI shown on device. The user will confirm (save) or discard "
                    "(cancel) by voice or by tapping. Wait for confirm_task_creation / "
                    "discard_task_creation — do NOT claim the task is saved yet."
                ),
            })

        if name == "discard_task_creation":
            return json.dumps({
                "ok": True,
                "device_task_dismiss": True,
                "message": "Task discarded — screen dismissed. Acknowledge briefly: 'Okay, cancelled.'",
            })

        if name == "confirm_task_creation":
            ok_appr, appr_err = require_user_approval(
                args.get("confirmed_by_user"), args.get("confirmation_phrase")
            )
            if not ok_appr:
                return json.dumps(appr_err)

            from services.tasks_service import (
                voice_create_task,
                TaskFidelityError,
            )
            title = str(args.get("title") or "").strip()
            if not title:
                return json.dumps({"ok": False, "error": "title_required"})
            due_raw  = str(args.get("due_date") or args.get("due_at") or "").strip()
            desc_raw = str(args.get("description") or args.get("detail") or "").strip()
            try:
                row = voice_create_task(
                    user_id=user_id,
                    title=title,
                    due_date=due_raw or None,
                    description=desc_raw or None,
                    confirm_duplicate=True,
                    source="voice",
                )
            except TaskFidelityError as exc:
                return json.dumps({"ok": False, "error": "task_fidelity", "detail": str(exc)})
            except Exception as exc:
                logger.warning("confirm_task_creation failed: %s", exc)
                return json.dumps({
                    "ok": False,
                    "device_task_dismiss": True,
                    "error": "task_create_failed",
                    "detail": str(exc),
                    "message": "Saving failed. Apologise and offer to try again.",
                })
            return json.dumps(
                {
                    "ok": True,
                    "device_task_dismiss": True,
                    "task": {
                        "id": row.get("id"),
                        "title": row.get("title"),
                        "due_at": row.get("due_at"),
                        "status": row.get("status"),
                    },
                    "truth_status": {
                        "writes_committed": True,
                        "note": "Task saved to user_commitments.",
                    },
                    "message": "Task saved. Confirm to the user: 'Done — it's on your list.'",
                },
                default=str,
            )

        if name == "show_calendar_event":
            ev_name   = str(args.get("name") or args.get("title") or "").strip() or None
            ev_date   = str(args.get("date") or "").strip() or None
            ev_time   = str(args.get("time") or "").strip() or None
            ev_id     = str(args.get("event_id") or "").strip() or None
            raw_dur   = args.get("duration_minutes")
            try:
                ev_dur = int(raw_dur) if raw_dur not in (None, "", 0) else None
            except (TypeError, ValueError):
                ev_dur = None
            attendees_mode = str(args.get("attendees_mode") or "replace").strip().lower()
            if attendees_mode not in ("replace", "append"):
                attendees_mode = "replace"
            # IMPORTANT: never infer fresh-reset implicitly.
            # The model may call show_calendar_event() with partial/no args while
            # resolving attendees; implicit reset would wipe title/date/time and
            # previously confirmed chips. A fresh draft reset must be explicit.
            reset = bool(args.get("reset")) if "reset" in args else False
            ev_attend = args.get("attendees")
            if isinstance(ev_attend, str):
                ev_attend = [ev_attend] if ev_attend.strip() else []
            elif not isinstance(ev_attend, list):
                ev_attend = None
            device_payload: dict[str, Any] = {}
            if ev_name is not None:
                device_payload["name"] = ev_name
            if ev_date is not None:
                device_payload["date"] = ev_date
            if ev_time is not None:
                device_payload["time"] = ev_time
            if ev_dur is not None:
                device_payload["duration_minutes"] = ev_dur
            if ev_attend is not None:
                device_payload["attendees"] = [str(a).strip() for a in ev_attend if str(a).strip()]
                device_payload["attendees_mode"] = attendees_mode
            if ev_id is not None:
                device_payload["event_id"] = ev_id
            device_payload["reset"] = reset
            return json.dumps({
                "ok": True,
                "device_calendar_event": device_payload,
                "message": (
                    "Calendar event UI shown on device. The user will confirm (create) or discard "
                    "(cancel) by voice or by tapping. Wait for confirm_calendar_event / "
                    "discard_calendar_event — do NOT claim the event is created yet."
                ),
            })

        if name == "discard_calendar_event":
            return json.dumps({
                "ok": True,
                # Dict payload tells the device this was a cancel (no navigation
                # to the calendar screen). Truthy so the dismiss directive fires.
                "device_calendar_event_dismiss": {"created": False},
                "message": "Event discarded — screen dismissed. Acknowledge briefly: 'Okay, cancelled.'",
            })

        if name == "confirm_calendar_event":
            ok_appr, appr_err = require_user_approval(
                args.get("confirmed_by_user"), args.get("confirmation_phrase")
            )
            if not ok_appr:
                return json.dumps(appr_err)

            from routes.integrations import get_credentials_for_provider
            from services.calendar import (
                create_event,
                default_calendar_tz_name,
                list_upcoming_events,
                update_event,
            )

            ev_name = str(args.get("name") or args.get("title") or "").strip()
            ev_date = str(args.get("date") or "").strip()
            ev_time = str(args.get("time") or "").strip()
            event_id = str(args.get("event_id") or "").strip() or None
            attendees_replace_flag = bool(args.get("attendees_replace", False))
            # Support post-send editing even when the model does not pass event_id:
            # if there's a concrete edit instruction, locate by title/date and patch.
            is_edit = bool(
                event_id
                or attendees_replace_flag
                or args.get("attendees_remove")
                or args.get("attendees_add")
                or args.get("new_time")
                or args.get("new_date")
                or args.get("new_duration_minutes")
                or args.get("edit_mode")
            )
            if not ev_name:
                return json.dumps({"ok": False, "error": "name_required"})
            duration = args.get("duration_minutes")
            try:
                duration = int(duration) if duration else 30
            except (TypeError, ValueError):
                duration = 30

            # Extract bare email addresses from "Name <email>" or plain strings.
            raw_attend = args.get("attendees")
            if isinstance(raw_attend, str):
                raw_attend = [raw_attend]
            elif not isinstance(raw_attend, list):
                raw_attend = []
            emails: list[str] = []
            for a in raw_attend:
                s = str(a or "").strip()
                if not s:
                    continue
                if "<" in s and ">" in s:
                    s = s[s.find("<") + 1:s.find(">")].strip()
                if "@" in s and s not in emails:
                    emails.append(s)
            raw_add = args.get("attendees_add")
            raw_remove = args.get("attendees_remove")
            add_list = raw_add if isinstance(raw_add, list) else []
            remove_list = raw_remove if isinstance(raw_remove, list) else []
            attendees_add: list[str] = []
            attendees_remove: list[str] = []
            for item in add_list:
                s = str(item or "").strip()
                if "<" in s and ">" in s:
                    s = s[s.find("<") + 1:s.find(">")].strip()
                if "@" in s and s not in attendees_add:
                    attendees_add.append(s)
            for item in remove_list:
                s = str(item or "").strip()
                if "<" in s and ">" in s:
                    s = s[s.find("<") + 1:s.find(">")].strip()
                if "@" in s and s not in attendees_remove:
                    attendees_remove.append(s)

            # Normalise the time to HH:MM (24-hour) for the calendar service.
            hhmm = _normalise_hhmm(ev_time)

            creds = get_credentials_for_provider(user_id, "calendar")
            if not creds:
                return json.dumps({
                    "ok": False,
                    "device_calendar_event_dismiss": {"created": False},
                    "error": "calendar_not_connected",
                    "message": (
                        "Google Calendar is not connected. Tell the user to connect it in "
                        "Settings → Integrations."
                    ),
                })
            try:
                if is_edit:
                    resolved_event_id = event_id
                    if not resolved_event_id and ev_name:
                        try:
                            rows = list_upcoming_events(
                                creds,
                                max_results=50,
                                date_filter=(ev_date or None),
                                days_ahead=1 if ev_date else 30,
                                timezone=default_calendar_tz_name(),
                            )
                            match = next(
                                (
                                    r for r in rows
                                    if ev_name.lower() in str(r.get("summary") or "").lower()
                                ),
                                None,
                            )
                            if isinstance(match, dict):
                                resolved_event_id = str(match.get("id") or "").strip() or None
                        except Exception:
                            resolved_event_id = None
                    # Build a combined ISO start so the time-of-day moves too.
                    new_start = f"{ev_date}T{hhmm}:00" if (ev_date and hhmm) else None
                    result = update_event(
                        creds,
                        event_id=resolved_event_id,
                        title_hint=(ev_name if not resolved_event_id else None),
                        date_hint=(ev_date or None if not resolved_event_id else None),
                        timezone=default_calendar_tz_name(),
                        title=ev_name,
                        new_start_time=new_start,
                        new_date=(ev_date or None if not new_start else None),
                        new_duration_minutes=duration,
                        attendees_replace=(emails if attendees_replace_flag else None),
                        attendees_add=((attendees_add or emails) if not attendees_replace_flag else None),
                        attendees_remove=attendees_remove or None,
                    )
                else:
                    result = create_event(
                        credentials=creds,
                        title=ev_name,
                        duration_minutes=duration,
                        attendees=emails or None,
                        timezone=default_calendar_tz_name(),
                        start_date=ev_date or None,
                        start_time_hhmm=hhmm,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("confirm_calendar_event failed: %s", exc)
                return json.dumps({
                    "ok": False,
                    "device_calendar_event_dismiss": {"created": False},
                    "error": "calendar_save_failed",
                    "detail": str(exc),
                    "message": "Saving the event failed. Apologise and offer to try again.",
                })

            # Resolve the event date for the device so it can open the Calendar
            # screen on the right day. Prefer the API result, fall back to args.
            result_date = ev_date or None
            try:
                start = (result.get("start") or {})
                start_iso = start.get("dateTime") or start.get("date") or ""
                if start_iso:
                    result_date = str(start_iso)[:10]
            except Exception:
                pass

            action = "updated" if is_edit else "created"
            return json.dumps(
                {
                    "ok": True,
                    # Dict payload: tells the device the event was saved and on
                    # which day, so it navigates to the Calendar screen.
                    "device_calendar_event_dismiss": {
                        "created": True,
                        "action": action,
                        "date": result_date,
                    },
                    "event": {
                        "id": result.get("id"),
                        "title": ev_name,
                        "htmlLink": result.get("htmlLink"),
                    },
                    "truth_status": {
                        "writes_committed": True,
                        "note": f"Event {action} on the user's Google Calendar.",
                    },
                    "message": (
                        f"Event {action}. Confirm to the user: 'Done — it's on your "
                        "calendar. You can see it there now.'"
                    ),
                },
                default=str,
            )

        if name == "create_task":
            from services.tasks_service import (
                voice_create_task,
                TaskFidelityError,
                SimilarTaskExistsError,
            )
            title = str(args.get("title") or "").strip()
            if not title:
                return json.dumps({"error": "title_required"})
            due_raw = str(args.get("due_date") or args.get("due_at") or "").strip()
            desc_raw = str(args.get("description") or args.get("detail") or "").strip()
            confirm_dupe = bool(args.get("confirm_duplicate"))
            try:
                row = voice_create_task(
                    user_id=user_id,
                    title=title,
                    due_date=due_raw or None,
                    description=desc_raw or None,
                    confirm_duplicate=confirm_dupe,
                    source="voice",
                )
            except SimilarTaskExistsError as exc:
                return json.dumps(
                    {
                        "warning": "similar_task_exists",
                        "similar_task": exc.similar,
                        "message": (
                            "A similar task already exists. Read the existing task's title back to "
                            "the user and ask whether to add this as a new task or update the "
                            "existing one. Call create_task again with confirm_duplicate=true if "
                            "the user wants a new task; or call update_task with the existing "
                            "task_id if they want to update it."
                        ),
                        "truth_status": {"writes_committed": False, "note": "No task created."},
                    },
                    default=str,
                )
            except TaskFidelityError as exc:
                return json.dumps({"error": "task_fidelity", "detail": str(exc)})
            except Exception as exc:
                logger.warning("create_task failed: %s", exc)
                return json.dumps({"error": "task_create_failed", "detail": str(exc)})
            return json.dumps(
                {
                    "task": {
                        "id": row.get("id"),
                        "title": row.get("title"),
                        "due_at": row.get("due_at"),
                        "status": row.get("status"),
                        "detail": row.get("detail"),
                    },
                    "truth_status": {
                        "writes_committed": True,
                        "note": "Task saved to user_commitments.",
                    },
                },
                default=str,
            )

        if name == "list_tasks":
            from tools.commitments_tool import commitment_list_for_user
            from tools.base_tool import ToolError as _ToolErrLocal
            status_raw = str(args.get("status") or "").strip().lower()
            try:
                lim = int(args.get("limit") or 30)
            except (TypeError, ValueError):
                lim = 30
            try:
                res = commitment_list_for_user(
                    user_id, max_results=lim, status=status_raw
                )
            except _ToolErrLocal as exc:
                return json.dumps({"error": "list_tasks_failed", "detail": str(exc)})
            tasks = [
                {
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "due_at": r.get("due_at"),
                    "status": r.get("status"),
                    "detail": (r.get("detail") or "")[:200],
                    "source": r.get("source"),
                }
                for r in (res.get("commitments") or [])
            ]
            return json.dumps(
                {"tasks": tasks, "count": len(tasks)},
                default=str,
            )

        if name == "update_task":
            from services.tasks_service import (
                voice_update_task,
                TaskNotFoundError,
                AmbiguousTaskMatchError,
                TaskFidelityError,
            )
            task_id = str(args.get("task_id") or "").strip() or None
            title_match = str(args.get("title_match") or "").strip() or None
            status_new = str(args.get("status") or "").strip().lower() or None
            due_new = str(args.get("due_date") or args.get("due_at") or "").strip() or None
            if not task_id and not title_match:
                return json.dumps(
                    {"error": "id_or_title_match_required", "detail": "Pass task_id or title_match."}
                )
            if not status_new and not due_new:
                return json.dumps(
                    {"error": "nothing_to_update", "detail": "Pass status and/or due_date."}
                )
            try:
                row = voice_update_task(
                    user_id=user_id,
                    task_id=task_id,
                    title_match=title_match,
                    status=status_new,
                    due_date=due_new,
                )
            except TaskNotFoundError:
                return json.dumps(
                    {
                        "error": "task_not_found",
                        "detail": "No matching task found. Try list_tasks first to find the right one.",
                    }
                )
            except AmbiguousTaskMatchError as exc:
                return json.dumps(
                    {
                        "warning": "ambiguous_match",
                        "candidates": exc.candidates,
                        "message": (
                            "Multiple tasks match — read the candidate titles to the user and ask "
                            "which one they meant, then call update_task with task_id."
                        ),
                    },
                    default=str,
                )
            except TaskFidelityError as exc:
                return json.dumps({"error": "task_fidelity", "detail": str(exc)})
            except Exception as exc:
                logger.warning("update_task failed: %s", exc)
                return json.dumps({"error": "task_update_failed", "detail": str(exc)})
            return json.dumps(
                {
                    "task": {
                        "id": row.get("id"),
                        "title": row.get("title"),
                        "due_at": row.get("due_at"),
                        "status": row.get("status"),
                    },
                    "truth_status": {"writes_committed": True, "note": "Task updated."},
                },
                default=str,
            )

        if name == "extract_tasks_from_emails":
            from services.tasks_service import extract_tasks_from_emails_sync
            q = str(args.get("query") or "").strip() or None
            try:
                max_emails = int(args.get("max_emails") or 5)
            except (TypeError, ValueError):
                max_emails = 5
            try:
                result = extract_tasks_from_emails_sync(
                    user_id=user_id,
                    query=q,
                    max_emails=max(1, min(max_emails, 15)),
                )
            except Exception as exc:
                logger.warning("extract_tasks_from_emails failed: %s", exc)
                return json.dumps(
                    {"error": "extraction_failed", "detail": str(exc)}
                )
            return json.dumps(result, default=str)

        if name == "lookup_email_contacts":
            from services.contacts_service import lookup_contacts
            query = str(args.get("query") or "").strip()
            if not query:
                return json.dumps({"error": "query_required"})
            try:
                matches = lookup_contacts(user_id, query, limit=5)
            except Exception as exc:
                logger.warning("lookup_email_contacts failed: %s", exc)
                return json.dumps({"error": "lookup_failed", "detail": str(exc)})
            if not matches:
                return json.dumps({
                    "contacts": [],
                    "count": 0,
                    "note": f"No known contacts found for '{query}'. Ask the user to spell out the full email address.",
                })
            return json.dumps({
                "contacts": matches,
                "count": len(matches),
                "note": "Read these options to the user and ask which one applies, or if it's a new address.",
            })

        if name == "show_recipient_picker":
            from services.contacts_service import lookup_contacts
            query = str(args.get("query") or "").strip()
            field = str(args.get("field") or "to").strip().lower()
            if field not in ("to", "cc", "bcc", "attendee"):
                field = "to"
            if not query:
                return json.dumps({"error": "query_required"})
            try:
                matches = lookup_contacts(user_id, query, limit=8)
            except Exception as exc:
                logger.warning("show_recipient_picker lookup failed: %s", exc)
                matches = []
            # Fallback: the local contacts book only learns addresses lazily.
            # If nothing matches yet, harvest lifetime correspondents for this
            # name from the user's Gmail — BOTH people they've emailed (To/Cc on
            # sent mail) and people who've emailed them (From) — store them
            # per-user, then look up again. This is what makes "email Shiva"
            # resolve even if we've only ever sent to Shiva, not received.
            if not matches:
                try:
                    from routes.integrations import get_credentials_for_provider
                    from services.contacts_service import harvest_from_gmail
                    creds = get_credentials_for_provider(user_id, "gmail")
                    if creds:
                        safe_q = query.replace('"', "")
                        harvest_from_gmail(
                            user_id,
                            creds,
                            query=(
                                f'from:"{safe_q}" OR to:"{safe_q}" OR cc:"{safe_q}"'
                            ),
                            max_messages=60,
                        )
                        matches = lookup_contacts(user_id, query, limit=8)
                except Exception as exc:
                    logger.info(
                        "show_recipient_picker gmail fallback skipped: %s", exc
                    )
            candidates = [
                {"name": m.get("name") or "", "email": m.get("email") or ""}
                for m in matches
                if m.get("email")
            ]
            # Prevent duplicate cards for the same address.
            seen_emails: set[str] = set()
            uniq: list[dict[str, str]] = []
            for c in candidates:
                em = str(c.get("email") or "").strip().lower()
                if not em or em in seen_emails:
                    continue
                seen_emails.add(em)
                uniq.append(c)
            candidates = uniq
            payload = {
                "ok": True,
                "contacts": candidates,
                "count": len(candidates),
            }
            if not candidates:
                # No match → do NOT pop a contact card on the device (an empty
                # picker confuses the user). The model tells them out loud and
                # asks for the address instead.
                payload["note"] = (
                    f"There are no contacts associated with {query}. Please provide the email address. "
                    "Do NOT show a picker. Take the dictated address and call remember_contact. "
                    "NEVER guess an address."
                )
            else:
                # Only render the on-device picker when we actually have cards.
                payload["device_recipient_picker"] = {
                    "query": query, "candidates": candidates, "field": field,
                }
                payload["none_option_label"] = "None of these"
                if len(candidates) == 1:
                    payload["note"] = (
                        "One match — the card is shown on screen. Confirm with the user "
                        "(voice or tap) BEFORE drafting. Never assume it is correct."
                    )
                else:
                    payload["note"] = (
                        "Multiple matches — all cards are shown on screen. Read them out and ask "
                        "which one (the user may say 'the first one' / a name, or tap a card). "
                        "Confirm before drafting."
                    )
            return json.dumps(payload, default=str)

        if name == "remember_contact":
            # VALIDATE ONLY — do NOT persist here. If we wrote on every call, a
            # mis-heard address would stay in the book alongside the corrected
            # one. The address is saved automatically (store-on-use) the moment
            # it actually goes into a draft / send / cc-add, so only the final
            # confirmed address is ever remembered.
            email_addr = str(args.get("email") or "").strip()
            person = str(args.get("name") or "").strip()
            parsed_name, parsed_addr = _parse_email_address(email_addr)
            if not parsed_addr:
                return json.dumps({
                    "error": "invalid_email",
                    "detail": (
                        f"'{email_addr}' is not a valid email address. Ask the user to repeat it."
                    ),
                })
            return json.dumps({
                "ok": True,
                "contact": {"name": person or parsed_name, "email": parsed_addr},
                "note": (
                    "Address is valid. Read it back letter-by-letter to confirm. It will be "
                    "remembered automatically once you use it in the draft — do NOT store a "
                    "different address unless the user corrects this one."
                ),
            })

        if name == "show_email_draft":
            # IMPORTANT: emit ONLY the fields the model actually passed in this
            # call. The device popup MERGES the payload onto the current draft —
            # if we always sent to/cc/bcc/subject/body (even empty), a
            # single-field edit would blank the other fields on screen. By
            # omitting absent keys the device keeps their existing values, so an
            # edit shows the full draft with just the changed field updated.
            draft = {
                "state": str(args.get("state") or "drafting").strip().lower() or "drafting",
            }
            if "to" in args:
                draft["to"] = _normalize_recipient_list(args.get("to"))
            if "cc" in args:
                draft["cc"] = _normalize_recipient_list(args.get("cc"))
            if "bcc" in args:
                draft["bcc"] = _normalize_recipient_list(args.get("bcc"))
            if "subject" in args:
                draft["subject"] = str(args.get("subject") or "")
            if "body" in args:
                draft["body"] = str(args.get("body") or "")
            draft_id = str(args.get("draft_id") or "").strip()
            if draft_id:
                draft["draft_id"] = draft_id
            # Reply-all: fill the full participant list (To + every Cc) from the
            # thread so the popup shows exactly who the reply will reach. These
            # addresses go ONLY into device_email_draft (a device-only surface);
            # the device strips device_email_draft before echoing this result to
            # the model, so the model never receives the concrete recipients and
            # can't use them to mis-send. The actual send always routes through
            # gmail_reply_all, which recomputes recipients itself.
            reply_all_thread_id = str(args.get("reply_all_thread_id") or "").strip()
            if reply_all_thread_id:
                try:
                    from routes.integrations import get_credentials_for_provider
                    from services.gmail import compute_reply_all_recipients
                    creds = get_credentials_for_provider(user_id, "gmail")
                    if creds:
                        recips = compute_reply_all_recipients(creds, reply_all_thread_id)
                        if recips.get("to"):
                            draft["to"] = _normalize_recipient_list([recips["to"]])
                        if recips.get("cc"):
                            draft["cc"] = _normalize_recipient_list(recips["cc"])
                        if recips.get("subject") and not draft.get("subject"):
                            draft["subject"] = recips["subject"]
                except Exception as exc:
                    logger.info("show_email_draft reply-all fill skipped: %s", exc)
            return json.dumps({
                "ok": True,
                "device_email_draft": draft,
                "note": (
                    "Draft popup updated on screen (fields you omit are kept as-is). Keep your "
                    "spoken reply short and do NOT read the body aloud unless the user asks."
                ),
            }, default=str)

        if name == "fetch_and_show_email":
            query = str(args.get("query") or "").strip()
            try:
                from routes.integrations import get_credentials_for_provider
                from services.gmail import list_recent_messages, get_message_with_attachments
                from zoneinfo import ZoneInfo
                from datetime import datetime as _dt

                creds = get_credentials_for_provider(user_id, "gmail")
                if not creds:
                    return json.dumps({"ok": False, "error": "Gmail not connected. Ask the user to connect Gmail in the web dashboard."})

                messages = list_recent_messages(creds, max_results=1, q=query or "")
                if not messages:
                    return json.dumps({
                        "ok": False,
                        "error": "no_email_found",
                        "message": "No email found matching that description. Ask the user to be more specific.",
                    })

                msg = messages[0]
                message_id = msg.get("id", "")
                full = get_message_with_attachments(creds, message_id)

                # Parse sender
                from_raw = full.get("from", "")
                sender_name, sender_email_addr = _email_utils.parseaddr(from_raw)
                if not sender_name:
                    sender_name = sender_email_addr.split("@")[0] if sender_email_addr else from_raw
                sender_name = sender_name.strip()
                sender_initial = sender_name[0].upper() if sender_name else "?"

                # Format time
                date_raw = full.get("date", "")
                time_str = ""
                try:
                    tz_name = "Asia/Kolkata"
                    try:
                        from services.calendar import default_calendar_tz_name as _dtz
                        tz_name = _dtz()
                    except Exception:
                        pass
                    dt_parsed = _email_utils.parsedate_to_datetime(date_raw)
                    dt_local = dt_parsed.astimezone(ZoneInfo(tz_name))
                    today_date = _dt.now(ZoneInfo(tz_name)).date()
                    if dt_local.date() == today_date:
                        time_str = dt_local.strftime("%I:%M %p").lstrip("0")
                    else:
                        time_str = dt_local.strftime("%b %-d")
                except Exception:
                    time_str = date_raw[:16] if date_raw else ""

                _body_raw = (full.get("body", "") or full.get("snippet", "")
                             or msg.get("snippet", "") or "")
                # Normalise line-endings: strip \r so Kivy Label never
                # renders carriage-returns as tofu boxes.
                _body_clean = _body_raw.replace("\r\n", "\n").replace("\r", "\n")

                view = {
                    "subject":        full.get("subject", "") or msg.get("subject", ""),
                    "sender_name":    sender_name,
                    "sender_initial": sender_initial,
                    "sender_email":   sender_email_addr or "",
                    "time":           time_str,
                    "recipient_label": "to me",
                    "body":           _body_clean,
                }
                sender_display = sender_name or sender_email_addr or "unknown sender"
                thread_id_val = full.get("threadId") or msg.get("threadId") or ""
                to_val = full.get("to", "")
                cc_val = full.get("cc", "")
                return json.dumps({
                    "ok": True,
                    "device_email_view": view,
                    "sender": sender_display,
                    "from_email": sender_email_addr or "",
                    "subject": view["subject"],
                    "thread_id": thread_id_val,
                    "message_id": message_id,
                    "to": to_val,
                    "cc": cc_val,
                    "note": (
                        f"Email from {sender_display} is now displayed on screen. "
                        "Say 'Here\\'s that email from [sender].' and keep it short — "
                        "do NOT read the body aloud unless the user explicitly asks. "
                        "IMPORTANT: thread_id is the Gmail thread id for this email. "
                        "When the user wants to reply, ALWAYS treat it as reply-all: "
                        "call show_email_draft(reply_all_thread_id=<thread_id>, state='drafting', ...) "
                        "so the server fills every participant into To + Cc on screen. "
                        "Then when sending, phrase assistant_intent as "
                        "'Reply all to thread <thread_id> with body: ...' — NEVER as a new email send."
                    ),
                }, default=str)
            except Exception as exc:
                logger.info("fetch_and_show_email failed: %s", exc, exc_info=True)
                return json.dumps({"ok": False, "error": str(exc)})

        if name == "show_email_view":
            view: dict = {}
            for key in ("subject", "sender_name", "sender_initial", "sender_email",
                        "time", "recipient_label", "body"):
                if key in args:
                    view[key] = str(args.get(key) or "")
            return json.dumps({
                "ok": True,
                "device_email_view": view,
                "note": (
                    "Email is now displayed on screen. Keep your spoken reply short — "
                    "do NOT read the full body aloud unless the user asks."
                ),
            }, default=str)

        # ── Personal Notes handlers ───────────────────────────────────────────
        if name == "note_create":
            from services.notes_service import upsert_note
            title = str(args.get("title") or "").strip()
            content = str(args.get("content") or "").strip()
            if not title and not content:
                return json.dumps({"error": "title or content is required to create a note"})
            tags_raw = args.get("tags")
            tags = [str(t) for t in (tags_raw if isinstance(tags_raw, list) else []) if t]
            pinned = bool(args.get("pinned", False))
            try:
                row = upsert_note(user_id, {
                    "title": title,
                    "content": content,
                    "tags": tags,
                    "pinned": pinned,
                    "source": "voice",
                })
                logger.info("NOTE_CREATE ok user=%s note_id=%s title=%r", user_id, row.get("id"), row.get("title"))
                try:
                    from services.mem0_service import maybe_ingest_note
                    maybe_ingest_note(user_id, row)
                except Exception:
                    logger.debug("note_create: mem0 ingest failed", exc_info=True)
                return json.dumps({"ok": True, "note_id": row["id"], "title": row["title"],
                                   "message": "Note saved successfully."}, default=str)
            except ValueError as exc:
                return json.dumps({"error": str(exc)})

        if name == "note_list":
            from services.notes_service import list_notes
            search = str(args.get("search") or "").strip() or None
            tag = str(args.get("tag") or "").strip() or None
            pinned_only = bool(args.get("pinned_only", False))
            date_from = str(args.get("date_from") or "").strip() or None
            date_to = str(args.get("date_to") or "").strip() or None
            try:
                limit = max(1, min(int(args.get("limit") or 10), 50))
            except (TypeError, ValueError):
                limit = 10
            try:
                rows = list_notes(user_id, limit=limit, pinned_only=pinned_only,
                                  tag_filter=tag, search=search,
                                  date_from=date_from, date_to=date_to)
                logger.info("NOTE_LIST user=%s count=%d (search=%r tag=%r pinned_only=%s date=%s..%s)",
                            user_id, len(rows), search, tag, pinned_only, date_from, date_to)
                # Strip internal DB fields that the LLM has no reason to read aloud
                # (source/user_id were causing the agent to say "voice note" verbatim).
                _drop = {"source", "user_id"}
                clean = [{k: v for k, v in r.items() if k not in _drop} for r in rows]
                return json.dumps({"notes": clean, "count": len(clean)}, default=str)
            except Exception as exc:
                logger.warning("note_list failed user=%s: %s", user_id, exc, exc_info=True)
                return json.dumps({"error": str(exc)[:300]})

        if name == "note_update":
            from services.notes_service import upsert_note
            note_id = str(args.get("note_id") or "").strip()
            if not note_id:
                return json.dumps({"error": "note_id required"})
            payload: dict = {"note_id": note_id}
            if "title" in args:
                payload["title"] = str(args["title"] or "").strip()
            if "content" in args:
                payload["content"] = str(args["content"] or "")
                payload["append"] = bool(args.get("append", False))
            if "tags" in args:
                tags_raw = args["tags"]
                payload["tags"] = [str(t) for t in (tags_raw if isinstance(tags_raw, list) else []) if t]
            if "pinned" in args:
                payload["pinned"] = bool(args["pinned"])
            try:
                row = upsert_note(user_id, payload)
                try:
                    from services.mem0_service import maybe_ingest_note
                    maybe_ingest_note(user_id, row)
                except Exception:
                    logger.debug("note_update: mem0 ingest failed", exc_info=True)
                return json.dumps({"ok": True, "note_id": row["id"], "title": row["title"],
                                   "message": "Note updated."}, default=str)
            except ValueError as exc:
                return json.dumps({"error": str(exc)})

        if name == "note_delete":
            from services.notes_service import delete_note
            note_id = str(args.get("note_id") or "").strip()
            if not note_id:
                return json.dumps({"error": "note_id required"})
            try:
                deleted = delete_note(user_id, note_id)
                if deleted:
                    try:
                        from services.mem0_service import delete_note_from_mem0
                        delete_note_from_mem0(user_id, note_id)
                    except Exception:
                        logger.debug("note_delete: mem0 cleanup failed", exc_info=True)
                return json.dumps({"ok": deleted, "message": "Note deleted." if deleted else "Note not found."})
            except Exception as exc:
                logger.warning("note_delete failed: %s", exc)
                return json.dumps({"error": str(exc)[:300]})

        return json.dumps({"error": "unknown_tool", "name": name})
    except Exception:
        logger.exception("realtime_voice_tool failed name=%s", name)
        return json.dumps({"error": "tool_execution_failed", "name": name})
