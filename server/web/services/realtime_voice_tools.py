<<<<<<< Updated upstream
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
    {"home", "calendar", "emails", "meetings", "tasks", "morning_brief", "settings", "mic_test"}
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
                        "For screen=emails: today | all | unread | sent | drafts."
                    ),
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
            "RESOLVE AND CONFIRM an email recipient the user referred to by NAME. This is the "
            "REQUIRED first step whenever the user wants to email / draft / reply / forward to a "
            "person without spelling the full address (e.g. 'email Rahul', 'draft a mail to Neha'). "
            "The server searches ALL known contact sources (sent mail, received mail, draft "
            "recipients, calendar attendees) ranked by interaction frequency, and displays the "
            "matching contacts as tappable cards on the device screen so the user can confirm by "
            "voice OR touch. "
            "CRITICAL: you MUST call this and wait for the user to confirm BEFORE drafting — never "
            "assume a recipient, even when only one match exists. "
            "Behavior based on the returned 'count': "
            "1 match -> say e.g. 'I found [Name] at [email] — is that the right person?' and wait for "
            "a yes / tap. "
            ">1 match -> say e.g. 'I found a few: (1) [Name] [email], (2) [Name] [email]. Which one?' "
            "and wait for a spoken choice ('the first one' / a name) or a tap. "
            "0 matches -> say EXACTLY 'Sorry, I couldn't find anyone by that name. Could you tell me "
            "their email address?', take the dictated address, then call remember_contact. "
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
            "Display or update the email draft popup on the device screen. This popup is the PRIMARY "
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
            if raw in ("task", "action_items", "todo", "todos"):
                raw = "tasks"
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
            return json.dumps(payload)

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
            payload = {
                "ok": True,
                "device_recipient_picker": {"query": query, "candidates": candidates},
                "contacts": candidates,
                "count": len(candidates),
            }
            if not candidates:
                payload["note"] = (
                    f"No known contacts match '{query}'. Say EXACTLY: 'Sorry, I couldn't find "
                    "anyone by that name. Could you tell me their email address?' Then take the "
                    "dictated address and call remember_contact. NEVER guess an address."
                )
            elif len(candidates) == 1:
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

        return json.dumps({"error": "unknown_tool", "name": name})
    except Exception:
        logger.exception("realtime_voice_tool failed name=%s", name)
        return json.dumps({"error": "tool_execution_failed", "name": name})
=======
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
    {"home", "calendar", "emails", "meetings", "tasks", "morning_brief", "settings", "mic_test"}
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
                        "For screen=emails: today | all | unread | sent | drafts."
                    ),
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
            "RESOLVE AND CONFIRM a person the user referred to by NAME — for email (draft / send / "
            "reply / forward) AND for calendar invite attendees ('invite Rahul', 'schedule with Neha', "
            "'add Priya to the meeting'). This is the REQUIRED first step whenever a person is named "
            "without spelling their full email address. "
            "The server searches ALL known contact sources (sent mail, received mail, draft recipients, "
            "calendar attendees) ranked by interaction frequency, and displays the matching contacts as "
            "tappable cards on the device screen so the user can confirm by voice OR touch. "
            "CRITICAL: you MUST call this and wait for the user to confirm BEFORE drafting an email or "
            "adding a calendar attendee — never assume a recipient, even when only one match exists. "
            "Behavior based on the returned 'count': "
            "1 match -> say e.g. 'I found [Name] at [email] — is that the right person?' and wait for "
            "a yes / tap. "
            ">1 match -> say e.g. 'I found a few: (1) [Name] [email], (2) [Name] [email]. Which one?' "
            "and wait for a spoken choice ('the first one' / a name) or a tap. "
            "0 matches -> say EXACTLY 'Sorry, I couldn't find anyone by that name. Could you tell me "
            "their email address?', take the dictated address, then call remember_contact. "
            "For multiple names (e.g. 'invite Rahul and Neha'), call show_recipient_picker once per "
            "person and confirm each before continuing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The person's name (or partial name/email) the user said, e.g. 'Rahul', 'Neha Sharma'.",
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
            "Display or update the email draft popup on the device screen. This popup is the PRIMARY "
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
            if raw in ("task", "action_items", "todo", "todos"):
                raw = "tasks"
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
            return json.dumps(payload)

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
            payload = {
                "ok": True,
                "device_recipient_picker": {"query": query, "candidates": candidates},
                "contacts": candidates,
                "count": len(candidates),
            }
            if not candidates:
                payload["note"] = (
                    f"No known contacts match '{query}'. Say EXACTLY: 'Sorry, I couldn't find "
                    "anyone by that name. Could you tell me their email address?' Then take the "
                    "dictated address and call remember_contact. NEVER guess an address."
                )
            elif len(candidates) == 1:
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

        return json.dumps({"error": "unknown_tool", "name": name})
    except Exception:
        logger.exception("realtime_voice_tool failed name=%s", name)
        return json.dumps({"error": "tool_execution_failed", "name": name})
>>>>>>> Stashed changes
