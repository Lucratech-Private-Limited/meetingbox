"""
Unit tests for the Research Agent (research_agent).

Covers:
  - research_agent.json schema: required fields, tool_policies vs tools alignment, triggers
  - _filter_steps_for_agent: unknown tools dropped; all 7 research tools pass
  - Heuristic planner picks the right tool for representative phrases
  - Dispatch smoke (no LLM, no internet):
      * Each research tool executes directly (no pending actions queued)
      * Assistant lines are populated with a human-readable summary
  - Service-layer extensions (with httpx mocked):
      * web_search Brave path + DDG HTML fallback
      * currency_convert normalizes aliases and applies rate
      * deep_research depth classification + sub-query planning fallback
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_agent_json() -> dict[str, Any]:
    p = Path(__file__).resolve().parent.parent / "agents" / "research_agent.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _bootstrap_db(tmp_path, monkeypatch):
    db = tmp_path / f"res_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("MEETINGBOX_DB_PATH", str(db))
    monkeypatch.setenv("MEETINGBOX_MEM0_DISABLE", "1")

    import database
    importlib.reload(database)
    database.init_database()

    uid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO users (id, username, password_hash, email, role, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (uid, "u1", "x", "u@test", "user", "2020-01-01"),
    )
    conn.commit()
    conn.close()

    import assistant_service as asvc
    importlib.reload(asvc)
    return uid, asvc


# ---------------------------------------------------------------------------
# 1. JSON schema sanity
# ---------------------------------------------------------------------------

class TestResearchAgentJson:
    def test_required_top_level_fields(self):
        doc = _load_agent_json()
        for f in ("id", "name", "tools", "tool_policies", "guidelines"):
            assert f in doc, f"Missing required field: {f}"

    def test_id_and_name(self):
        doc = _load_agent_json()
        assert doc["id"] == "research_agent"
        assert "Research" in doc["name"]

    def test_tools_list_complete(self):
        doc = _load_agent_json()
        expected = {
            "research_web_search",
            "research_news",
            "research_weather",
            "research_currency_convert",
            "research_stock_price",
            "research_sports_score",
            "research_deep_research",
        }
        assert set(doc["tools"]) == expected

    def test_tool_policies_match_tools(self):
        doc = _load_agent_json()
        tools = set(doc["tools"])
        policies = set(doc["tool_policies"].keys())
        assert tools == policies, f"Mismatch: only-in-tools={tools - policies}, only-in-policies={policies - tools}"

    def test_all_tools_read_only(self):
        """Every research tool must be direct-execute (no approval queue)."""
        doc = _load_agent_json()
        for t, p in doc["tool_policies"].items():
            assert p["requires_approval"] is False, f"{t} should not require approval"

    def test_memory_context_off(self):
        """Research is stateless / fresh — memory injection just adds noise."""
        doc = _load_agent_json()
        assert doc.get("memory_context") is False

    def test_triggers_cover_required_phrases(self):
        doc = _load_agent_json()
        trig_lower = {t.lower() for t in doc["triggers"]}
        for phrase in (
            "weather", "forecast", "aqi",
            "look up", "search the web",
            "latest news", "headlines",
            "convert", "exchange rate", "in rupees", "in dollars",
            "stock", "stock price",
            "live score", "ipl",
            "research", "deep research", "deep dive", "tell me about",
        ):
            assert phrase in trig_lower, f"Missing trigger: {phrase}"

    def test_priority_lower_than_calendar(self):
        """Research should be lower priority than calendar so date-y phrases route to calendar."""
        doc = _load_agent_json()
        cal = json.loads((Path(__file__).resolve().parent.parent / "agents" / "calendar_agent.json").read_text(encoding="utf-8"))
        assert int(doc["priority"]) < int(cal["priority"]), (
            f"research_agent priority {doc['priority']} must be < calendar_agent priority {cal['priority']}"
        )


# ---------------------------------------------------------------------------
# 2. _filter_steps_for_agent
# ---------------------------------------------------------------------------

class TestFilterStepsForAgent:
    def test_all_research_tools_kept(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        steps = [{"tool": t, "args": {}} for t in (
            "research_web_search",
            "research_news",
            "research_weather",
            "research_currency_convert",
            "research_stock_price",
            "research_sports_score",
            "research_deep_research",
        )]
        out = asvc._filter_steps_for_agent("research_agent", steps)
        assert {s["tool"] for s in out} == {s["tool"] for s in steps}

    def test_unknown_tool_dropped(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        out = asvc._filter_steps_for_agent("research_agent", [
            {"tool": "research_web_search"},
            {"tool": "calendar_create_event"},
            {"tool": "made_up_tool"},
        ])
        kept = {s["tool"] for s in out}
        assert kept == {"research_web_search"}


# ---------------------------------------------------------------------------
# 3. Heuristic planner picks the right tool
# ---------------------------------------------------------------------------

class TestHeuristicPlanner:
    @pytest.mark.parametrize("msg,expected_tool", [
        ("what's the weather today", "research_weather"),
        ("temperature in Mumbai", "research_weather"),
        ("aqi right now", "research_weather"),
        ("convert 100 usd to inr", "research_currency_convert"),
        ("100 dollars in rupees", "research_currency_convert"),
        ("AAPL stock price", "research_stock_price"),
        ("share price of TCS", "research_stock_price"),
        ("ipl live score", "research_sports_score"),
        ("cricket score india vs australia", "research_sports_score"),
        ("deep research on quantum computing", "research_deep_research"),
        ("deep dive into the AI safety debate", "research_deep_research"),
        ("comprehensive research on tariffs", "research_deep_research"),
        ("latest news headlines", "research_news"),
        ("breaking news on the election", "research_news"),
        ("what is langchain", "research_web_search"),
        ("tell me about the moon landing", "research_web_search"),
    ])
    def test_planner_routes_correctly(self, tmp_path, monkeypatch, msg, expected_tool):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        steps = asvc._heuristic_research_plan(msg)
        assert steps and steps[0]["tool"] == expected_tool, f"{msg!r} -> {steps}"
        # All research tools must be marked read-only
        assert steps[0]["is_write"] is False

    def test_currency_parses_amount(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        steps = asvc._heuristic_research_plan("convert 250.50 usd to inr")
        assert steps[0]["tool"] == "research_currency_convert"
        assert steps[0]["args"]["amount"] == 250.50
        assert steps[0]["args"]["from"].lower() == "usd"
        assert steps[0]["args"]["to"].lower() == "inr"

    def test_deep_research_depth_inference(self, tmp_path, monkeypatch):
        _, asvc = _bootstrap_db(tmp_path, monkeypatch)
        steps = asvc._heuristic_research_plan("comprehensive research on tariffs")
        assert steps[0]["args"].get("depth") == "deep"

        steps = asvc._heuristic_research_plan("thorough research on tariffs")
        assert steps[0]["args"].get("depth") == "medium"


# ---------------------------------------------------------------------------
# 4. Dispatch — research tools execute directly and produce assistant text
# ---------------------------------------------------------------------------

class TestDispatchDirect:
    def test_web_search_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        fake = {
            "source": "brave",
            "query": "langchain",
            "quick_answer": "LangChain is a framework for building LLM apps.",
            "results": [{"title": "LangChain Docs", "url": "https://langchain.com", "snippet": "Docs."}],
        }
        with patch.object(asvc, "research_web_search_from_payload", return_value=fake) as m_ws, \
             patch.object(asvc, "plan_research_steps", return_value=[
                {"tool": "research_web_search", "args": {"query": "langchain"}, "is_write": False}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="research_agent", method="triggers")):
            r = asvc.process_assistant_intent(message="what is langchain", user_id=uid, meeting_id=None)
        m_ws.assert_called_once()
        assert any(t.get("tool") == "research_web_search" and "result" in t for t in r["tool_results"])
        assert not r["pending_actions"], "research is read-only — nothing should queue"
        assert "LangChain" in r["assistant_message"]

    def test_weather_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        fake = {
            "city": "Bengaluru",
            "temperature_c": 28, "feels_like_c": 30,
            "condition": "Partly cloudy",
            "high_c": 32, "low_c": 22, "humidity_pct": 60, "wind_kph": 8.1, "aqi": 78,
        }
        with patch.object(asvc, "research_weather_from_payload", return_value=fake) as m_w, \
             patch.object(asvc, "plan_research_steps", return_value=[
                {"tool": "research_weather", "args": {}, "is_write": False}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="research_agent", method="triggers")):
            r = asvc.process_assistant_intent(message="what's the weather", user_id=uid, meeting_id=None)
        m_w.assert_called_once()
        assert not r["pending_actions"]
        # Lead with city + temp
        assert "Bengaluru" in r["assistant_message"]
        assert "28" in r["assistant_message"]
        assert "AQI" in r["assistant_message"]

    def test_currency_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        fake = {
            "source": "open.er-api",
            "amount": 100.0, "from": "USD", "to": "INR",
            "rate": 83.5, "converted": 8350.0, "as_of": "Mon, 26 May 2026 00:00:00 +0000",
        }
        with patch.object(asvc, "research_currency_convert_from_payload", return_value=fake) as m_cc, \
             patch.object(asvc, "plan_research_steps", return_value=[
                {"tool": "research_currency_convert", "args": {"amount": 100.0, "from": "USD", "to": "INR"}, "is_write": False}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="research_agent", method="triggers")):
            r = asvc.process_assistant_intent(message="convert 100 usd to inr", user_id=uid, meeting_id=None)
        m_cc.assert_called_once()
        assert not r["pending_actions"]
        assert "8350" in r["assistant_message"]
        assert "INR" in r["assistant_message"]

    def test_stock_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        fake = {
            "ticker": "AAPL",
            "query": "AAPL stock price today",
            "source": "brave",
            "quick_answer": "AAPL is trading around $192.50.",
            "results": [],
        }
        with patch.object(asvc, "research_stock_price_from_payload", return_value=fake), \
             patch.object(asvc, "plan_research_steps", return_value=[
                {"tool": "research_stock_price", "args": {"ticker": "AAPL"}, "is_write": False}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="research_agent", method="triggers")):
            r = asvc.process_assistant_intent(message="AAPL stock price", user_id=uid, meeting_id=None)
        assert not r["pending_actions"]
        assert "AAPL" in r["assistant_message"] and "192" in r["assistant_message"]

    def test_news_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        fake = {
            "source": "BBC News",
            "category": "top",
            "headlines": [
                {"title": "Headline One", "url": "http://x/1", "summary": "..."},
                {"title": "Headline Two", "url": "http://x/2", "summary": "..."},
            ],
            "count": 2,
        }
        with patch.object(asvc, "research_news_from_payload", return_value=fake), \
             patch.object(asvc, "plan_research_steps", return_value=[
                {"tool": "research_news", "args": {"category": "top"}, "is_write": False}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="research_agent", method="triggers")):
            r = asvc.process_assistant_intent(message="latest news headlines", user_id=uid, meeting_id=None)
        assert not r["pending_actions"]
        assert "Headline One" in r["assistant_message"]

    def test_sports_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        fake = {
            "query": "ipl live score",
            "source": "brave",
            "quick_answer": "RCB beat CSK by 8 wickets.",
            "results": [],
        }
        with patch.object(asvc, "research_sports_score_from_payload", return_value=fake), \
             patch.object(asvc, "plan_research_steps", return_value=[
                {"tool": "research_sports_score", "args": {"query": "ipl"}, "is_write": False}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="research_agent", method="triggers")):
            r = asvc.process_assistant_intent(message="ipl live score", user_id=uid, meeting_id=None)
        assert not r["pending_actions"]
        assert "RCB" in r["assistant_message"]

    def test_deep_research_executes_directly(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        fake = {
            "topic": "quantum computing 2026",
            "depth": "shallow",
            "sub_queries": ["quantum computing 2026", "quantum computing latest"],
            "sources_count": 4,
            "sources": [
                {"title": "QC overview", "url": "http://x/a", "snippet": "..."},
                {"title": "Roadmap", "url": "http://x/b", "snippet": "..."},
            ],
            "synthesis": "TL;DR: quantum computing is still early.\n\nDetails follow [1][2].",
            "elapsed_ms": 4200,
        }
        with patch.object(asvc, "research_deep_research_from_payload", return_value=fake), \
             patch.object(asvc, "plan_research_steps", return_value=[
                {"tool": "research_deep_research", "args": {"topic": "quantum computing 2026"}, "is_write": False}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="research_agent", method="triggers")):
            r = asvc.process_assistant_intent(message="research quantum computing", user_id=uid, meeting_id=None)
        assert not r["pending_actions"]
        msg = r["assistant_message"]
        assert "TL;DR" in msg
        assert "Sources" in msg or "[1]" in msg

    def test_tool_error_surfaces_in_assistant_lines(self, tmp_path, monkeypatch):
        uid, asvc = _bootstrap_db(tmp_path, monkeypatch)
        from tools.base_tool import ToolError

        def _raises(_args):
            raise ToolError("Web search is temporarily unavailable.")

        with patch.object(asvc, "research_web_search_from_payload", side_effect=_raises), \
             patch.object(asvc, "plan_research_steps", return_value=[
                {"tool": "research_web_search", "args": {"query": "x"}, "is_write": False}
             ]), \
             patch.object(asvc, "route_intent", return_value=asvc.RouteResult(agent_id="research_agent", method="triggers")):
            r = asvc.process_assistant_intent(message="search for x", user_id=uid, meeting_id=None)
        assert any(t.get("error") for t in r["tool_results"])
        assert "temporarily unavailable" in r["assistant_message"].lower()


# ---------------------------------------------------------------------------
# 5. Service-layer extensions (HTTP mocked)
# ---------------------------------------------------------------------------

class TestServiceLayer:
    def _httpx_client_mock(self, responses_by_url: dict[str, Any]):
        """Build a mock client whose .get(url, ...) returns a fake Response by URL prefix."""
        def _resp_for(url):
            class _R:
                def __init__(self, payload, status=200, text=""):
                    self._payload = payload
                    self.status_code = status
                    self.text = text
                def raise_for_status(self):
                    if self.status_code >= 400:
                        raise RuntimeError(f"HTTP {self.status_code}")
                    return self
                def json(self):
                    return self._payload
            for prefix, payload in responses_by_url.items():
                if url.startswith(prefix):
                    if isinstance(payload, tuple):
                        body, kind = payload
                        if kind == "text":
                            return _R({}, 200, body)
                    return _R(payload, 200, "")
            return _R({}, 404, "")

        cm = MagicMock()
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get = MagicMock(side_effect=lambda url, **kw: _resp_for(url))
        cm.return_value = client
        return cm

    def test_currency_convert_normalizes_aliases(self, monkeypatch):
        from services import research as rs

        monkeypatch.setattr(rs.httpx, "Client", self._httpx_client_mock({
            "https://open.er-api.com/v6/latest/USD": {
                "rates": {"INR": 83.5, "EUR": 0.92},
                "time_last_update_utc": "Mon, 26 May 2026 00:00:00 +0000",
            },
        }))
        out = rs.fetch_currency_convert_sync(100, "dollar", "rupee")
        assert out["from"] == "USD"
        assert out["to"] == "INR"
        assert out["rate"] == 83.5
        assert out["converted"] == round(100 * 83.5, 4)

    def test_currency_convert_unknown_target(self, monkeypatch):
        from services import research as rs

        monkeypatch.setattr(rs.httpx, "Client", self._httpx_client_mock({
            "https://open.er-api.com/v6/latest/USD": {"rates": {"INR": 83.5}, "time_last_update_utc": ""},
        }))
        out = rs.fetch_currency_convert_sync(100, "USD", "XYZ")
        assert out.get("error") == "unknown_currency"

    def test_web_search_brave_path(self, monkeypatch):
        from services import research as rs

        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fake-key")
        monkeypatch.setattr(rs.httpx, "Client", self._httpx_client_mock({
            "https://api.search.brave.com/res/v1/web/search": {
                "web": {"results": [
                    {"title": "T1", "url": "u1", "description": "snip 1"},
                    {"title": "T2", "url": "u2", "description": "snip 2"},
                ]},
                "infobox": {"description": "factual answer about langchain"},
            },
        }))
        out = rs.fetch_web_search_sync("langchain")
        assert out["source"] == "brave"
        assert len(out["results"]) == 2
        assert out["quick_answer"].startswith("factual")

    def test_web_search_ddg_html_fallback(self, monkeypatch):
        """When BRAVE_SEARCH_API_KEY is missing AND the query is not 'news-y',
        the fallback chain hits DDG HTML. Mock that path."""
        from services import research as rs

        monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
        html = (
            '<div><a class="result__a" href="x">Title One</a>'
            '<a class="result__url" href="x">example.com</a>'
            '<a class="result__snippet" href="x">Snippet body</a></div>'
        )
        monkeypatch.setattr(rs.httpx, "Client", self._httpx_client_mock({
            "https://html.duckduckgo.com/html/": (html, "text"),
        }))
        out = rs.fetch_web_search_sync("how does photosynthesis work")
        assert out["source"] == "duckduckgo_html"
        assert out["results"] and "Snippet body" in out["results"][0]["snippet"]

    def test_deep_research_depth_classification(self):
        from services import research as rs
        assert rs._classify_depth("deep dive into AI safety") == "deep"
        assert rs._classify_depth("exhaustive research on tariffs") == "deep"
        assert rs._classify_depth("in-depth research on tariffs") == "medium"
        assert rs._classify_depth("research on tariffs") == "medium"
        assert rs._classify_depth("tariffs explained") == "shallow"
        assert rs._classify_depth("anything", override="deep") == "deep"

    def test_deep_research_full_flow_with_mocked_web_search(self, monkeypatch):
        """deep_research orchestrates: planner sub-queries + N web_search calls + synthesis."""
        from services import research as rs

        # Force heuristic sub-query planning (no Anthropic available in test env)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        # Mock web_search to return distinct snippets per query.
        call_log: list[str] = []

        def _fake_ws(query, num_results=5):
            call_log.append(query)
            return {
                "source": "test",
                "query": query,
                "results": [
                    {"title": f"R for {query}", "url": f"http://x/{len(call_log)}", "snippet": f"snippet for {query}"},
                ],
            }

        monkeypatch.setattr(rs, "fetch_web_search_sync", _fake_ws)
        out = rs.fetch_deep_research_sync("quantum computing", depth="shallow")
        assert out["depth"] == "shallow"
        assert len(out["sub_queries"]) == 3, out["sub_queries"]
        assert out["sources_count"] >= 1
        assert isinstance(out["synthesis"], str) and out["synthesis"]
