"""
Research / web-search service.

Consolidates the web-search, news, weather, AQI, currency, stock-price, sports-score,
and deep-research helpers used by both the realtime voice path and the chat-based
research_agent. All functions are sync (called from `run_in_executor` on the voice
path; called directly by the chat assistant_service dispatcher).

External dependencies (all free, no required keys):
  - Brave Search API (optional via BRAVE_SEARCH_API_KEY; otherwise falls back to scrapers)
  - Google News RSS (no key)
  - DuckDuckGo HTML + Instant Answer (no key)
  - Open-Meteo (weather + AQI, no key)
  - BBC News RSS (no key)
  - open.er-api.com (currency, no key)
  - Anthropic (deep_research synthesis, uses AI_MODEL env var)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Weather
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


def _geocode_city(city: str) -> tuple[float, float, str] | None:
    """Resolve a city name to (lat, lon, display_name). Returns None on failure."""
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "en", "format": "json"},
            )
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results") or []
        if not results:
            return None
        r = results[0]
        return float(r["latitude"]), float(r["longitude"]), r.get("name") or city
    except Exception as exc:
        logger.warning("Geocode failed for %r: %s", city, exc)
        return None


def fetch_weather_sync(city: str | None = None) -> dict:
    """Get current weather + AQI. Defaults to env WEATHER_LAT/LON/CITY if no city given."""
    if city and city.strip():
        geo = _geocode_city(city.strip())
        if geo:
            lat, lon, display_name = geo
        else:
            return {"city": city, "error": "geocode_failed", "note": f"Could not locate '{city}'."}
    else:
        lat = float(os.getenv("WEATHER_LAT", "12.9716"))
        lon = float(os.getenv("WEATHER_LON", "77.5946"))
        display_name = os.getenv("WEATHER_CITY", "Bengaluru")

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
        "city": display_name,
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
# Google News RSS
# ---------------------------------------------------------------------------

def fetch_google_news_rss_sync(query: str, num_results: int = 6) -> dict | None:
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
# DuckDuckGo HTML scraper
# ---------------------------------------------------------------------------

def fetch_ddg_html_sync(query: str, num_results: int = 5) -> dict | None:
    """Scrape DuckDuckGo HTML search for snippet-level results. Returns None on failure."""
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            )
            resp.raise_for_status()
        html = resp.text
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
        urls = re.findall(r'class="result__url"[^>]*>(.*?)</a>', html, re.DOTALL)

        def strip_tags(s: str) -> str:
            return re.sub(r"<[^>]+>", "", s).strip()

        results = []
        for i, snip in enumerate(snippets[:num_results]):
            title = strip_tags(titles[i]) if i < len(titles) else ""
            url = strip_tags(urls[i]) if i < len(urls) else ""
            snippet = strip_tags(snip)
            if snippet:
                results.append({"title": title, "url": url, "snippet": snippet})
        if not results:
            return None
        return {"source": "duckduckgo_html", "query": query, "results": results}
    except Exception as exc:
        logger.warning("DDG HTML scrape failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Web search — Brave → Google News RSS → DDG HTML → DDG JSON instant
# ---------------------------------------------------------------------------

def fetch_web_search_sync(query: str, num_results: int = 5) -> dict:
    brave_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()

    if brave_key:
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

    news_kw = any(w in query.lower() for w in (
        "news", "latest", "today", "recent", "update", "headline", "breaking",
        "ipl", "cricket", "football", "match", "score", "politics", "election",
        "market", "stock", "economy",
    ))
    if news_kw:
        gnews = fetch_google_news_rss_sync(query, num_results)
        if gnews:
            return gnews

    ddg_html = fetch_ddg_html_sync(query, num_results)
    if ddg_html:
        return ddg_html

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

    gnews_any = fetch_google_news_rss_sync(query, num_results)
    if gnews_any:
        return gnews_any

    return {
        "source": "unavailable",
        "query": query,
        "note": "All search backends failed. Add BRAVE_SEARCH_API_KEY for reliable results.",
        "results": [],
    }


# ---------------------------------------------------------------------------
# News headlines (BBC RSS by category)
# ---------------------------------------------------------------------------

_NEWS_FEEDS: dict[str, str] = {
    "top":        "https://feeds.bbci.co.uk/news/rss.xml",
    "world":      "https://feeds.bbci.co.uk/news/world/rss.xml",
    "technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "business":   "https://feeds.bbci.co.uk/news/business/rss.xml",
    "science":    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "health":     "https://feeds.bbci.co.uk/news/health/rss.xml",
}


def fetch_news_sync(category: str = "top", limit: int = 6, query: str | None = None) -> dict:
    """If query is given, route to Google News RSS by query. Otherwise BBC top/world/tech/etc."""
    q = (query or "").strip()
    if q:
        out = fetch_google_news_rss_sync(q, num_results=limit)
        if out is not None:
            return out
        return {"error": "news_unavailable", "query": q, "headlines": []}

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
            link = (item.findtext("link") or "").strip()
            if title:
                headlines.append({"title": title, "url": link, "summary": desc[:200] if desc else None})
        return {
            "source": "BBC News",
            "category": category,
            "headlines": headlines,
            "count": len(headlines),
        }
    except Exception as exc:
        logger.warning("News fetch failed: %s", exc)
        return {"error": "news_unavailable", "detail": str(exc), "headlines": []}


# ---------------------------------------------------------------------------
# Currency conversion (open.er-api.com — no key, daily rates)
# ---------------------------------------------------------------------------

_CURRENCY_ALIASES: dict[str, str] = {
    "dollar": "USD", "dollars": "USD", "usd": "USD", "$": "USD",
    "rupee": "INR", "rupees": "INR", "inr": "INR", "₹": "INR", "rs": "INR",
    "euro": "EUR", "euros": "EUR", "eur": "EUR", "€": "EUR",
    "pound": "GBP", "pounds": "GBP", "gbp": "GBP", "£": "GBP",
    "yen": "JPY", "jpy": "JPY", "¥": "JPY",
    "yuan": "CNY", "cny": "CNY", "rmb": "CNY",
    "won": "KRW", "krw": "KRW",
    "cad": "CAD", "aud": "AUD", "sgd": "SGD", "aed": "AED", "chf": "CHF",
}


def _normalize_currency(s: str) -> str:
    return _CURRENCY_ALIASES.get((s or "").strip().lower(), (s or "").strip().upper())


def fetch_currency_convert_sync(amount: float, from_ccy: str, to_ccy: str) -> dict:
    fc = _normalize_currency(from_ccy)
    tc = _normalize_currency(to_ccy)
    if not fc or not tc:
        return {"error": "missing_currency", "from": from_ccy, "to": to_ccy}
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(f"https://open.er-api.com/v6/latest/{fc}")
            resp.raise_for_status()
            data = resp.json()
        rates = data.get("rates") or {}
        rate = rates.get(tc)
        if rate is None:
            return {"error": "unknown_currency", "from": fc, "to": tc, "available_count": len(rates)}
        converted = float(amount) * float(rate)
        return {
            "source": "open.er-api",
            "amount": float(amount),
            "from": fc,
            "to": tc,
            "rate": float(rate),
            "converted": round(converted, 4),
            "as_of": data.get("time_last_update_utc") or "",
        }
    except Exception as exc:
        logger.warning("Currency convert failed: %s", exc)
        return {"error": "fetch_failed", "detail": str(exc), "from": fc, "to": tc}


# ---------------------------------------------------------------------------
# Stock price (uses web_search since user opted for it)
# ---------------------------------------------------------------------------

def fetch_stock_price_sync(ticker: str) -> dict:
    t = (ticker or "").strip().upper()
    if not t:
        return {"error": "missing_ticker"}
    # Use a focused query and surface the top snippet as the "price line".
    query = f"{t} stock price today"
    web = fetch_web_search_sync(query, num_results=4)
    out: dict = {
        "ticker": t,
        "query": query,
        "source": web.get("source", "web_search"),
        "quick_answer": web.get("quick_answer"),
        "results": web.get("results") or [],
    }
    return out


# ---------------------------------------------------------------------------
# Sports score (web_search with a focused query)
# ---------------------------------------------------------------------------

def fetch_sports_score_sync(query: str) -> dict:
    q = (query or "").strip()
    if not q:
        return {"error": "missing_query"}
    # Make the query explicit so search engines rank live score widgets
    augmented = q if any(k in q.lower() for k in ("score", "result", "vs ")) else f"{q} live score"
    web = fetch_web_search_sync(augmented, num_results=5)
    return {
        "query": augmented,
        "source": web.get("source", "web_search"),
        "quick_answer": web.get("quick_answer"),
        "results": web.get("results") or [],
    }


# ---------------------------------------------------------------------------
# Deep research — multi-step web_search + LLM synthesis
# ---------------------------------------------------------------------------

_DEPTH_PRESETS: dict[str, dict[str, int]] = {
    "shallow": {"sub_queries": 3, "snippets_per_query": 5, "synth_words": 200},
    "medium":  {"sub_queries": 5, "snippets_per_query": 7, "synth_words": 400},
    "deep":    {"sub_queries": 8, "snippets_per_query": 10, "synth_words": 800},
}


def _classify_depth(message: str, override: str | None = None) -> str:
    if override and override.strip().lower() in _DEPTH_PRESETS:
        return override.strip().lower()
    m = (message or "").lower()
    if any(k in m for k in ("deep dive", "exhaustive", "thorough research", "deep research", "full research", "comprehensive")):
        return "deep"
    if any(k in m for k in ("medium dive", "thorough", "in-depth", "detailed research", "research on")):
        return "medium"
    return "shallow"


def _plan_sub_queries(topic: str, n: int) -> list[str]:
    """Ask Claude to generate N focused sub-queries that together cover the topic."""
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError:
        return _heuristic_sub_queries(topic, n)
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return _heuristic_sub_queries(topic, n)
    try:
        client = Anthropic(api_key=key)
        prompt = (
            f"Break this research request into EXACTLY {n} distinct web search queries that "
            f"together cover the topic from different angles (e.g. background, current state, "
            f"key players, recent news, controversies, future outlook). Each query should be "
            f"a search-engine-friendly phrase, not a question. Return ONLY a JSON object "
            f"{{\"queries\": [\"...\", \"...\", ...]}}.\n\n"
            f"Research topic: {topic.strip()[:600]}"
        )
        model = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")
        resp = client.messages.create(model=model, max_tokens=400, messages=[{"role": "user", "content": prompt}])
        text = getattr(resp.content[0], "text", "") or ""
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            data = json.loads(match.group(0))
            qs = [str(q).strip() for q in (data.get("queries") or []) if str(q).strip()]
            if qs:
                return qs[:n]
    except Exception as exc:
        logger.warning("deep_research sub-query planning failed: %s", exc)
    return _heuristic_sub_queries(topic, n)


def _heuristic_sub_queries(topic: str, n: int) -> list[str]:
    """Simple fallback: derive sub-queries from the topic when LLM is unavailable."""
    base = topic.strip()
    angles = [
        base,
        f"{base} latest news",
        f"{base} overview",
        f"{base} explained",
        f"{base} pros and cons",
        f"{base} future outlook",
        f"{base} controversy",
        f"{base} key players",
    ]
    return angles[:n]


def _synthesize_research(topic: str, snippets: list[dict], target_words: int) -> str:
    """Use Claude to synthesize a structured answer with inline citation markers [1], [2]…"""
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError:
        return _heuristic_synthesis(topic, snippets, target_words)
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return _heuristic_synthesis(topic, snippets, target_words)
    try:
        client = Anthropic(api_key=key)
        compact = []
        for i, s in enumerate(snippets[:30], 1):
            line = f"[{i}] {s.get('title', '')[:120]} :: {s.get('snippet', '')[:300]}"
            url = s.get("url") or ""
            if url:
                line += f" ({url[:120]})"
            compact.append(line)
        blob = "\n".join(compact)
        prompt = (
            f"You are a research analyst. Synthesize the following web search snippets into a "
            f"focused, factual answer to the user's research topic in about {target_words} words. "
            f"Use inline citation markers like [1], [2] referencing the numbered sources below. "
            f"Begin with a 1-2 sentence TL;DR. Be concrete; flag contradictions; avoid fluff.\n\n"
            f"Topic: {topic.strip()[:600]}\n\n"
            f"Sources:\n{blob}\n"
        )
        model = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")
        max_tokens = min(2400, max(400, target_words * 4))
        resp = client.messages.create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
        text = getattr(resp.content[0], "text", "") or ""
        return text.strip() or _heuristic_synthesis(topic, snippets, target_words)
    except Exception as exc:
        logger.warning("deep_research synthesis failed: %s", exc)
        return _heuristic_synthesis(topic, snippets, target_words)


def _heuristic_synthesis(topic: str, snippets: list[dict], target_words: int) -> str:
    parts = [f"Quick rundown on '{topic}' from {len(snippets)} sources:"]
    for i, s in enumerate(snippets[:8], 1):
        title = s.get("title") or ""
        snip = s.get("snippet") or ""
        parts.append(f"[{i}] {title}: {snip[:160]}")
    return "\n".join(parts)


def fetch_deep_research_sync(topic: str, depth: str | None = None, original_message: str | None = None) -> dict:
    """
    Run multi-step web research:
      1. Plan N focused sub-queries
      2. Fetch web_search results for each
      3. Synthesize a structured answer with citations
    Returns: {topic, depth, sub_queries, sources, synthesis}.
    """
    if not (topic or "").strip():
        return {"error": "missing_topic"}

    depth_key = _classify_depth(original_message or topic, depth)
    cfg = _DEPTH_PRESETS[depth_key]
    started = time.time()

    sub_queries = _plan_sub_queries(topic, cfg["sub_queries"])
    sources: list[dict] = []
    for q in sub_queries:
        try:
            res = fetch_web_search_sync(q, num_results=cfg["snippets_per_query"])
        except Exception as exc:
            logger.warning("deep_research web_search failed for %r: %s", q, exc)
            continue
        for item in (res.get("results") or []):
            if not isinstance(item, dict):
                continue
            url = item.get("url") or ""
            # dedupe by URL when present, else by title+snippet hash
            key = url or (str(item.get("title")) + str(item.get("snippet")))[:200]
            if not any((s.get("url") and s["url"] == url) or (s.get("_key") == key) for s in sources):
                item = dict(item)
                item["_key"] = key
                item["_query"] = q
                sources.append(item)

    for s in sources:
        s.pop("_key", None)

    synthesis = _synthesize_research(topic, sources, cfg["synth_words"]) if sources else (
        "No web results came back for this research request. Try rephrasing the topic or check that web access is available."
    )

    return {
        "topic": topic.strip(),
        "depth": depth_key,
        "sub_queries": sub_queries,
        "sources_count": len(sources),
        "sources": sources,
        "synthesis": synthesis,
        "elapsed_ms": int((time.time() - started) * 1000),
    }
