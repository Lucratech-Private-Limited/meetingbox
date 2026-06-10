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
# Stock price — Yahoo Finance v8 chart endpoint (no key, free, very reliable)
# ---------------------------------------------------------------------------

# Common Indian-company → NSE ticker shortcuts so users can speak the company name.
_INDIAN_TICKER_ALIASES: dict[str, str] = {
    "tata steel": "TATASTEEL.NS",
    "reliance": "RELIANCE.NS",
    "reliance industries": "RELIANCE.NS",
    "tcs": "TCS.NS",
    "tata consultancy": "TCS.NS",
    "infosys": "INFY.NS",
    "infy": "INFY.NS",
    "hdfc bank": "HDFCBANK.NS",
    "icici bank": "ICICIBANK.NS",
    "sbi": "SBIN.NS",
    "state bank of india": "SBIN.NS",
    "axis bank": "AXISBANK.NS",
    "bharti airtel": "BHARTIARTL.NS",
    "airtel": "BHARTIARTL.NS",
    "wipro": "WIPRO.NS",
    "itc": "ITC.NS",
    "larsen": "LT.NS",
    "l&t": "LT.NS",
    "lt": "LT.NS",
    "mahindra": "M&M.NS",
    "maruti": "MARUTI.NS",
    "maruti suzuki": "MARUTI.NS",
    "adani": "ADANIENT.NS",
    "adani enterprises": "ADANIENT.NS",
    "asian paints": "ASIANPAINT.NS",
    "bajaj finance": "BAJFINANCE.NS",
    "tata motors": "TATAMOTORS.NS",
    "tata gold": "GOLDBEES.NS",  # popular Tata gold ETF
    "kotak": "KOTAKBANK.NS",
    "ongc": "ONGC.NS",
    "ntpc": "NTPC.NS",
    "powergrid": "POWERGRID.NS",
    "sun pharma": "SUNPHARMA.NS",
    # Indices
    "nifty": "^NSEI",
    "nifty 50": "^NSEI",
    "sensex": "^BSESN",
    "bank nifty": "^NSEBANK",
    "nifty bank": "^NSEBANK",
    "nasdaq": "^IXIC",
    "dow jones": "^DJI",
    "dow": "^DJI",
    "s&p 500": "^GSPC",
    "sp500": "^GSPC",
    "ftse": "^FTSE",
    "nikkei": "^N225",
}


def _resolve_ticker_symbol(raw: str) -> str:
    """Normalize a user-facing ticker/company name into a Yahoo Finance symbol.

    - Symbols with a dot suffix (.NS, .BO, .L, .HK …) or starting with '^' are used as-is.
    - Known Indian company aliases are translated to <SYM>.NS.
    - Otherwise the input is upper-cased and returned (works for AAPL, TSLA, MSFT …).
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("^") or "." in s:
        return s.upper().replace(" ", "")
    low = s.lower()
    if low in _INDIAN_TICKER_ALIASES:
        return _INDIAN_TICKER_ALIASES[low]
    return s.upper().replace(" ", "")


def _yahoo_quote_sync(symbol: str) -> dict | None:
    """Hit Yahoo Finance v8 chart endpoint for a single symbol.

    Returns a small dict with regularMarketPrice, previousClose, currency, and
    a few price-history points, or None on failure.
    """
    if not symbol:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        with httpx.Client(timeout=6.0, follow_redirects=True) as client:
            resp = client.get(
                url,
                params={"range": "1d", "interval": "5m"},
                headers={"User-Agent": "Mozilla/5.0 (MeetingBox)"},
            )
            resp.raise_for_status()
            data = resp.json()
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            return None
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        change_pct = None
        change_abs = None
        if prev not in (None, 0):
            try:
                change_abs = round(float(price) - float(prev), 4)
                change_pct = round((float(price) - float(prev)) / float(prev) * 100.0, 2)
            except Exception:
                pass
        return {
            "symbol": meta.get("symbol") or symbol,
            "exchange": meta.get("exchangeName"),
            "currency": meta.get("currency") or "",
            "price": round(float(price), 4),
            "previous_close": round(float(prev), 4) if prev is not None else None,
            "change_abs": change_abs,
            "change_pct": change_pct,
            "day_high": meta.get("regularMarketDayHigh"),
            "day_low": meta.get("regularMarketDayLow"),
            "market_state": meta.get("marketState"),  # PRE / REGULAR / POST / CLOSED
            "instrument_type": meta.get("instrumentType"),
            "as_of_epoch": meta.get("regularMarketTime"),
        }
    except Exception as exc:
        logger.warning("Yahoo quote failed for %r: %s", symbol, exc)
        return None


def fetch_stock_price_sync(ticker: str) -> dict:
    """Get a live quote.

    Tries Yahoo Finance first. If the resolved symbol fails and it has no
    suffix, retries with '.NS' (NSE) so users can say "TATASTEEL" or
    "tata steel" without knowing the exchange.

    Falls back to a focused web_search snippet only when Yahoo returns nothing.
    """
    raw = (ticker or "").strip()
    if not raw:
        return {"error": "missing_ticker"}

    symbol = _resolve_ticker_symbol(raw)

    quote = _yahoo_quote_sync(symbol)
    # If raw user input had no suffix and direct lookup failed, try .NS too.
    if quote is None and "." not in symbol and not symbol.startswith("^"):
        quote = _yahoo_quote_sync(f"{symbol}.NS")
        if quote is not None:
            symbol = f"{symbol}.NS"

    if quote is not None:
        return {
            "source": "yahoo_finance",
            "ticker": symbol,
            "input": raw,
            **quote,
        }

    # Fallback: snippet-based web search so the agent still has something to read.
    query = f"{raw} stock price today"
    web = fetch_web_search_sync(query, num_results=4)
    return {
        "source": web.get("source", "web_search"),
        "ticker": symbol,
        "input": raw,
        "query": query,
        "quick_answer": web.get("quick_answer"),
        "results": web.get("results") or [],
        "note": "Live quote unavailable — showing search snippets.",
    }


# ---------------------------------------------------------------------------
# Research papers — Semantic Scholar Graph API (no key, free, citations)
# ---------------------------------------------------------------------------

def _crossref_paper_search_sync(query: str, limit: int) -> dict:
    """Fallback paper search when Semantic Scholar is rate-limited.

    Crossref is the canonical DOI registrar — it indexes essentially every
    peer-reviewed paper with a DOI. Free, no key, generous rate-limits, and
    returns citation count via `is-referenced-by-count` plus venue / journal
    info via `container-title`. Reliable from server environments where
    arXiv export-api may be firewalled.
    """
    url = "https://api.crossref.org/works"
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        resp = client.get(
            url,
            params={
                "query.bibliographic": query,
                "rows": limit,
                "select": (
                    "DOI,title,author,issued,container-title,abstract,URL,"
                    "is-referenced-by-count,references-count,type"
                ),
            },
            headers={"User-Agent": "MeetingBox/1.0 (mailto:research@meetingbox.local)"},
        )
        resp.raise_for_status()
        body = resp.json()

    items = (body.get("message") or {}).get("items") or []
    papers: list[dict] = []
    for it in items[:limit]:
        title = ""
        ttls = it.get("title")
        if isinstance(ttls, list) and ttls:
            title = (ttls[0] or "").strip()

        authors_raw = it.get("author") or []
        authors: list[str] = []
        for a in authors_raw:
            if not isinstance(a, dict):
                continue
            given = (a.get("given") or "").strip()
            family = (a.get("family") or "").strip()
            if given and family:
                authors.append(f"{given} {family}")
            elif family:
                authors.append(family)
            elif a.get("name"):
                authors.append(str(a["name"]).strip())

        year = None
        issued = (it.get("issued") or {}).get("date-parts") or []
        if issued and isinstance(issued[0], list) and issued[0]:
            try:
                if issued[0][0] is not None:
                    year = int(issued[0][0])
            except (TypeError, ValueError):
                year = None

        venue = ""
        ct = it.get("container-title")
        if isinstance(ct, list) and ct:
            venue = (ct[0] or "").strip()

        doi = (it.get("DOI") or "").strip()
        paper_url = (it.get("URL") or (f"https://doi.org/{doi}" if doi else "")).strip()
        # Crossref abstracts are JATS XML — strip the wrapping tags for a clean
        # readable excerpt the voice agent can speak aloud.
        abstract_raw = it.get("abstract") or ""
        abstract_clean = ""
        if abstract_raw:
            import re as _re
            abstract_clean = _re.sub(r"<[^>]+>", " ", abstract_raw)
            abstract_clean = _re.sub(r"\s+", " ", abstract_clean).strip()

        short_cite = ""
        if authors and year:
            first = authors[0]
            short_cite = f"{first.split()[-1]} et al., {year}" if len(authors) > 1 else f"{first}, {year}"
        elif authors:
            short_cite = authors[0]

        papers.append({
            "title": title,
            "authors": authors[:8],
            "year": year,
            "venue": venue,
            "citation_count": it.get("is-referenced-by-count"),
            "reference_count": it.get("references-count"),
            "abstract": abstract_clean[:600],
            "url": paper_url,
            "doi": doi,
            "arxiv_id": "",
            "pdf_url": "",
            "short_citation": short_cite,
            "type": it.get("type"),
        })

    return {
        "source": "crossref",
        "query": query,
        "count": len(papers),
        "papers": papers,
        "note": "Results from Crossref (Semantic Scholar was unavailable).",
    }



def fetch_research_paper_sync(query: str, limit: int = 5) -> dict:
    """Find academic papers by free-text query.

    Uses the Semantic Scholar Graph API (no key). Returns title, authors, year,
    venue, citation count, abstract, paper URL and an inline citation-friendly
    short reference for each match.

    Semantic Scholar's anonymous tier is aggressively rate-limited; we do a
    short retry with backoff on 429/5xx, and an arXiv fallback so a single
    flaky upstream call doesn't leave the voice agent empty-handed.
    """
    import time as _time

    q = (query or "").strip()
    if not q:
        return {"error": "missing_query"}
    n = max(1, min(int(limit or 5), 10))

    fields = (
        "title,authors,year,venue,citationCount,referenceCount,"
        "abstract,url,externalIds,openAccessPdf"
    )

    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY") or os.getenv("S2_API_KEY")
    headers = {"User-Agent": "MeetingBox/1.0 (mailto:research@meetingbox.local)"}
    if api_key:
        headers["x-api-key"] = api_key

    # Build a relaxed alt-query — Semantic Scholar sometimes 429s on exotic
    # tokens like 'Placeit3D' but accepts a spaced/lowercased rephrase.
    import re as _re
    relaxed = _re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Za-z])", " ", q).lower()
    relaxed = _re.sub(r"\s+", " ", relaxed).strip()
    query_variants = [q] if relaxed == q.lower() else [q, relaxed]

    data = None
    last_err: str = ""
    for variant in query_variants:
        for attempt in range(3):
            try:
                with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                    resp = client.get(
                        "https://api.semanticscholar.org/graph/v1/paper/search",
                        params={"query": variant, "limit": n, "fields": fields},
                        headers=headers,
                    )
                    if resp.status_code == 429:
                        last_err = "rate_limited"
                        _time.sleep(1.5 * (attempt + 1))
                        continue
                    if 500 <= resp.status_code < 600:
                        last_err = f"http_{resp.status_code}"
                        _time.sleep(0.8 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    break
            except Exception as exc:
                last_err = str(exc) or type(exc).__name__
                _time.sleep(0.4 * (attempt + 1))
        if data is not None:
            break

    if data is None:
        logger.warning("Semantic Scholar search failed for %r: %s", q, last_err)
        # Fallback to Crossref — canonical DOI registry, very reliable.
        try:
            return _crossref_paper_search_sync(q, n)
        except Exception as exc2:
            logger.warning("Crossref fallback failed for %r: %s", q, exc2)
            return {
                "error": "fetch_failed",
                "detail": last_err or str(exc2),
                "query": q,
                "papers": [],
                "hint": (
                    "Both Semantic Scholar and Crossref were unavailable. "
                    "Try web_search for this paper instead."
                ),
            }

    papers: list[dict] = []
    for r in (data.get("data") or [])[:n]:
        if not isinstance(r, dict):
            continue
        authors = [a.get("name") for a in (r.get("authors") or []) if isinstance(a, dict) and a.get("name")]
        first_author = authors[0] if authors else ""
        year = r.get("year")
        venue = r.get("venue") or ""
        ext = r.get("externalIds") or {}
        doi = ext.get("DOI") or ""
        arxiv = ext.get("ArXiv") or ""
        oa = r.get("openAccessPdf") or {}
        pdf_url = oa.get("url") if isinstance(oa, dict) else None
        # Format a short citation marker the agent can read aloud
        short_cite = ""
        if first_author and year:
            short_cite = f"{first_author.split()[-1]} et al., {year}" if len(authors) > 1 else f"{first_author}, {year}"
        elif first_author:
            short_cite = first_author
        papers.append({
            "title": r.get("title") or "",
            "authors": authors[:8],
            "year": year,
            "venue": venue,
            "citation_count": r.get("citationCount"),
            "reference_count": r.get("referenceCount"),
            "abstract": (r.get("abstract") or "")[:600],
            "url": r.get("url") or "",
            "doi": doi,
            "arxiv_id": arxiv,
            "pdf_url": pdf_url,
            "short_citation": short_cite,
        })

    return {
        "source": "semantic_scholar",
        "query": q,
        "count": len(papers),
        "papers": papers,
    }


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
        model = os.getenv("AI_MODEL", "claude-sonnet-4-5-20250929")
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
        model = os.getenv("AI_MODEL", "claude-sonnet-4-5-20250929")
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
