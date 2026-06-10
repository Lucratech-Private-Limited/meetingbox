"""Research / web-search tool adapters used by the research_agent."""

from __future__ import annotations

import logging
from typing import Any

from services.research import (
    fetch_currency_convert_sync,
    fetch_deep_research_sync,
    fetch_news_sync,
    fetch_research_paper_sync,
    fetch_sports_score_sync,
    fetch_stock_price_sync,
    fetch_weather_sync,
    fetch_web_search_sync,
)
from tools.base_tool import ToolError

logger = logging.getLogger("meetingbox.research_tool")


def _require(payload: dict[str, Any] | None, *, key: str) -> Any:
    if not isinstance(payload, dict):
        raise ToolError(f"Missing payload for {key}.")
    val = payload.get(key)
    if val is None or (isinstance(val, str) and not val.strip()):
        raise ToolError(f"Missing required field '{key}'.")
    return val


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------

def research_web_search(query: str, num_results: int = 5) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        raise ToolError("Search query is required.")
    n = max(1, min(int(num_results or 5), 10))
    try:
        return fetch_web_search_sync(q, num_results=n)
    except Exception as exc:
        logger.warning("research_web_search failed: %s", exc)
        raise ToolError("Web search is temporarily unavailable. Try again in a moment.") from exc


def research_web_search_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    query = _require(payload, key="query")
    return research_web_search(query=str(query), num_results=int(payload.get("num_results") or 5))


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

def research_news(category: str = "top", limit: int = 6, query: str | None = None) -> dict[str, Any]:
    try:
        return fetch_news_sync(category=str(category or "top"), limit=max(1, min(int(limit or 6), 20)), query=query)
    except Exception as exc:
        logger.warning("research_news failed: %s", exc)
        raise ToolError("News feed is temporarily unavailable.") from exc


def research_news_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    return research_news(
        category=str(payload.get("category") or "top"),
        limit=int(payload.get("limit") or 6),
        query=(payload.get("query") or None),
    )


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

def research_weather(city: str | None = None) -> dict[str, Any]:
    try:
        return fetch_weather_sync(city=city)
    except Exception as exc:
        logger.warning("research_weather failed: %s", exc)
        raise ToolError("Weather service is temporarily unavailable.") from exc


def research_weather_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    return research_weather(city=(payload.get("city") or None))


# ---------------------------------------------------------------------------
# Currency conversion
# ---------------------------------------------------------------------------

def research_currency_convert(amount: float, from_ccy: str, to_ccy: str) -> dict[str, Any]:
    try:
        amt = float(amount)
    except Exception as exc:
        raise ToolError("Currency 'amount' must be a number.") from exc
    if not (from_ccy or "").strip():
        raise ToolError("Source currency is required (e.g. USD, INR).")
    if not (to_ccy or "").strip():
        raise ToolError("Target currency is required (e.g. USD, INR).")
    try:
        return fetch_currency_convert_sync(amount=amt, from_ccy=str(from_ccy), to_ccy=str(to_ccy))
    except Exception as exc:
        logger.warning("research_currency_convert failed: %s", exc)
        raise ToolError("Currency service is temporarily unavailable.") from exc


def research_currency_convert_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    return research_currency_convert(
        amount=payload.get("amount", 1.0),
        from_ccy=str(payload.get("from") or payload.get("from_currency") or ""),
        to_ccy=str(payload.get("to") or payload.get("to_currency") or ""),
    )


# ---------------------------------------------------------------------------
# Stock price
# ---------------------------------------------------------------------------

def research_stock_price(ticker: str) -> dict[str, Any]:
    t = (ticker or "").strip()
    if not t:
        raise ToolError("Stock ticker is required (e.g. AAPL, RELIANCE.NS).")
    try:
        return fetch_stock_price_sync(t)
    except Exception as exc:
        logger.warning("research_stock_price failed: %s", exc)
        raise ToolError("Stock-price lookup is temporarily unavailable.") from exc


def research_stock_price_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    return research_stock_price(ticker=str(payload.get("ticker") or payload.get("symbol") or ""))


# ---------------------------------------------------------------------------
# Sports score
# ---------------------------------------------------------------------------

def research_sports_score(query: str) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        raise ToolError("Provide a match or team query for the score lookup.")
    try:
        return fetch_sports_score_sync(q)
    except Exception as exc:
        logger.warning("research_sports_score failed: %s", exc)
        raise ToolError("Score lookup is temporarily unavailable.") from exc


def research_sports_score_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    return research_sports_score(query=str(payload.get("query") or payload.get("match") or ""))


# ---------------------------------------------------------------------------
# Deep research
# ---------------------------------------------------------------------------

def research_deep_research(topic: str, depth: str | None = None, original_message: str | None = None) -> dict[str, Any]:
    t = (topic or "").strip()
    if not t:
        raise ToolError("Provide a research topic.")
    try:
        return fetch_deep_research_sync(topic=t, depth=depth, original_message=original_message)
    except Exception as exc:
        logger.warning("research_deep_research failed: %s", exc)
        raise ToolError("Deep research is temporarily unavailable. Try again in a moment.") from exc


def research_deep_research_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    return research_deep_research(
        topic=str(payload.get("topic") or payload.get("query") or ""),
        depth=(payload.get("depth") or None),
        original_message=(payload.get("original_message") or None),
    )


# ---------------------------------------------------------------------------
# Research papers (Semantic Scholar)
# ---------------------------------------------------------------------------

def research_paper_search(query: str, limit: int = 5) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        raise ToolError("Provide a search query for the paper lookup.")
    try:
        return fetch_research_paper_sync(query=q, limit=max(1, min(int(limit or 5), 10)))
    except Exception as exc:
        logger.warning("research_paper_search failed: %s", exc)
        raise ToolError("Paper search is temporarily unavailable.") from exc


def research_paper_search_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    return research_paper_search(
        query=str(payload.get("query") or payload.get("topic") or ""),
        limit=int(payload.get("limit") or 5),
    )
