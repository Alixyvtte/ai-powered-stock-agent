"""
Tests for the new features added to plan_node, market_node, and decide_node:
- plan_node: market_type detection, research_timestamp, generic fallback
- market_node: dual-source (yfinance + akshare) parallel fetch
- decide_node: source tier classification, evidence_confidence, all_missing_angles accumulation
- route_after_decide: 'insufficient' confidence routes to write_report
- write_node: research_timestamp in prompt, evidence_confidence warning
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List
from unittest.mock import MagicMock


# ─────────────────────────────────────────────────────────────
# Shared fake LLM
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Msg:
    content: str


class _FakeLLM:
    """Fake LLM that handles both structured-output and JSON-fallback paths.

    market_type and evidence_confidence are configurable per test.
    """

    def __init__(self, market_type: str = "us_equity", evidence_confidence: str = "high",
                 need_more: bool = False):
        self._schema = None
        self._market_type = market_type
        self._evidence_confidence = evidence_confidence
        self._need_more = need_more

    def with_structured_output(self, schema):
        self._schema = schema
        return self

    def invoke(self, prompt: str):
        schema = self._schema
        self._schema = None

        # ── JSON fallback path (DEEPSEEK_API_KEY set) ──
        if schema is None:
            if "Schema name: ResearchPlan" in prompt:
                return _Msg(
                    f'{{"topic":"Test research","tickers":["NVDA"],'
                    f'"subqueries":["NVDA catalysts"],"assumptions":["demand holds"],'
                    f'"market_type":"{self._market_type}"}}'
                )
            if "Schema name: EvidenceNotes" in prompt:
                return _Msg(
                    '{"items":[{"source_id":1,"claim":"revenue grew 10%",'
                    '"why_it_matters":"top-line growth signal"}]}'
                )
            if "Schema name: FollowupDecision" in prompt:
                need = str(self._need_more).lower()
                return _Msg(
                    f'{{"need_more":{need},"followup_queries":[],"missing_angles":[],'
                    f'"evidence_confidence":"{self._evidence_confidence}","refusal_reason":""}}'
                )
            # write_node (free-form)
            return _Msg("## Final Report\nResearch as of 2026-03-21T00:00:00Z.")

        # ── Structured output path (OpenAI) ──
        name = getattr(schema, "__name__", str(schema))

        if name == "ResearchPlan":
            return schema(
                topic="Test research",
                tickers=["NVDA"],
                subqueries=["NVDA catalysts"],
                assumptions=["demand holds"],
                market_type=self._market_type,
            )
        if name == "EvidenceNotes":
            return schema(items=[{
                "source_id": 1,
                "claim": "revenue grew 10%",
                "why_it_matters": "top-line growth signal",
            }])
        if name == "FollowupDecision":
            return schema(
                need_more=self._need_more,
                followup_queries=[],
                missing_angles=[],
                evidence_confidence=self._evidence_confidence,
                refusal_reason="",
            )
        return schema.model_validate({})


# ─────────────────────────────────────────────────────────────
# Helper: fake market data objects
# ─────────────────────────────────────────────────────────────

def _fake_market_snapshot(ticker: str):
    from stock_agent.tools.market_data import MarketSnapshot
    return MarketSnapshot(
        ticker=ticker, currency="USD", price=900.0, market_cap=2e12,
        trailing_pe=45.0, forward_pe=35.0, dividend_yield=None,
        week_52_high=950.0, week_52_low=400.0, beta=1.7,
        analyst_recommendation="buy", revenue_growth=0.22,
        retrieved_at="2026-03-21T00:00:00Z", source="yfinance",
    )


def _fake_a_share_snapshot(ticker: str):
    from stock_agent.tools.market_data import AShareSnapshot
    return AShareSnapshot(
        ticker=ticker, name="贵州茅台", industry="白酒",
        price=1800.0, market_cap_cny="22600亿",
        pe_ratio=30.0, pb_ratio=11.0, eps=55.0,
        retrieved_at="2026-03-21T00:00:00Z", source="akshare",
    )


def _fake_web_search(query, max_results=5, timeout_s=25):
    from stock_agent.tools.web_search import WebDocument
    return [WebDocument(
        title="NVDA Q4 earnings beat",
        url="https://reuters.com/nvda-q4",
        content="NVIDIA reported Q4 revenue of $22B, beating estimates.",
    )]


# ─────────────────────────────────────────────────────────────
# Test 1: _classify_source unit test
# ─────────────────────────────────────────────────────────────

def test_classify_source_primary():
    from stock_agent.graphs.deep_search_graph import _classify_source
    assert _classify_source("https://www.sec.gov/cgi-bin/browse-edgar") == "primary"
    assert _classify_source("https://fred.stlouisfed.org/series/GDP") == "primary"
    assert _classify_source("https://cninfo.com.cn/new/disclosure") == "primary"
    assert _classify_source("https://www.csrc.gov.cn/notice") == "primary"
    assert _classify_source("https://sse.com.cn/disclosure/listedinfo") == "primary"


def test_classify_source_secondary():
    from stock_agent.graphs.deep_search_graph import _classify_source
    assert _classify_source("https://www.reuters.com/markets/nvda") == "secondary"
    assert _classify_source("https://www.bloomberg.com/news/articles") == "secondary"
    assert _classify_source("https://www.cls.cn/detail/123") == "secondary"


def test_classify_source_aggregator():
    from stock_agent.graphs.deep_search_graph import _classify_source
    assert _classify_source("https://some-blog.com/nvda-analysis") == "aggregator"
    assert _classify_source("") == "aggregator"
    assert _classify_source(None) == "aggregator"


# ─────────────────────────────────────────────────────────────
# Test 2: plan_node sets research_timestamp and market_type
# ─────────────────────────────────────────────────────────────

def test_plan_node_sets_research_timestamp_and_market_type(monkeypatch):
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod
    from stock_agent.tools.web_search import WebDocument

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _FakeLLM(market_type="us_equity"))
    monkeypatch.setattr(gmod, "web_search", _fake_web_search)
    monkeypatch.setattr(gmod, "fetch_market_snapshot", _fake_market_snapshot)
    monkeypatch.setattr(gmod, "fetch_a_share_snapshot", _fake_a_share_snapshot)

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=1, max_results_per_query=1))
    out = graph.invoke({"query": "研究NVDA", "max_iterations": 1})

    # research_timestamp must be an ISO 8601 string
    ts = out.get("research_timestamp", "")
    assert ts, "research_timestamp should be set"
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts), f"unexpected format: {ts}"

    # market_type must be propagated into plan
    plan = out.get("plan") or {}
    assert plan.get("market_type") == "us_equity"


def test_plan_node_initializes_all_new_state_fields(monkeypatch):
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _FakeLLM())
    monkeypatch.setattr(gmod, "web_search", _fake_web_search)
    monkeypatch.setattr(gmod, "fetch_market_snapshot", _fake_market_snapshot)
    monkeypatch.setattr(gmod, "fetch_a_share_snapshot", _fake_a_share_snapshot)

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=1, max_results_per_query=1))
    out = graph.invoke({"query": "test query", "max_iterations": 1})

    assert "research_timestamp" in out
    assert "evidence_confidence" in out
    assert "all_missing_angles" in out
    assert isinstance(out["all_missing_angles"], list)


# ─────────────────────────────────────────────────────────────
# Test 3: market_node calls both yfinance and akshare
# ─────────────────────────────────────────────────────────────

def test_market_node_calls_both_sources(monkeypatch):
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    yf_calls = []
    ak_calls = []

    def tracked_yf(ticker):
        yf_calls.append(ticker)
        return _fake_market_snapshot(ticker)

    def tracked_ak(ticker):
        ak_calls.append(ticker)
        return _fake_a_share_snapshot(ticker)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _FakeLLM())
    monkeypatch.setattr(gmod, "web_search", _fake_web_search)
    monkeypatch.setattr(gmod, "fetch_market_snapshot", tracked_yf)
    monkeypatch.setattr(gmod, "fetch_a_share_snapshot", tracked_ak)

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=1, max_results_per_query=1))
    out = graph.invoke({"query": "研究NVDA", "max_iterations": 1})

    # Both fetchers must have been called
    assert len(yf_calls) >= 1, "fetch_market_snapshot (yfinance) was not called"
    assert len(ak_calls) >= 1, "fetch_a_share_snapshot (akshare) was not called"

    # market dict should be nested {ticker: {yfinance: ..., akshare: ...}}
    market = out.get("market") or {}
    assert market, "market should not be empty"
    ticker_key = list(market.keys())[0]
    assert "yfinance" in market[ticker_key] or "_fetch_yf" in str(market[ticker_key])
    assert "akshare" in market[ticker_key] or "_fetch_ak" in str(market[ticker_key])


def test_market_node_no_tickers_returns_empty(monkeypatch):
    """When plan has no tickers, market should be {}."""
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    class _NoTickerLLM(_FakeLLM):
        def invoke(self, prompt):
            schema = self._schema
            self._schema = None
            if schema is None:
                if "Schema name: ResearchPlan" in prompt:
                    return _Msg(
                        '{"topic":"Macro rates","tickers":[],'
                        '"subqueries":["Fed rate outlook"],"assumptions":[],'
                        '"market_type":"macro"}'
                    )
                if "Schema name: EvidenceNotes" in prompt:
                    return _Msg('{"items":[{"source_id":1,"claim":"Fed holds rates","why_it_matters":"macro"}]}')
                if "Schema name: FollowupDecision" in prompt:
                    return _Msg('{"need_more":false,"followup_queries":[],"missing_angles":[],'
                                '"evidence_confidence":"medium","refusal_reason":""}')
                return _Msg("macro report")
            name = getattr(schema, "__name__", "")
            if name == "ResearchPlan":
                return schema(topic="Macro rates", tickers=[], subqueries=["Fed rate outlook"],
                              assumptions=[], market_type="macro")
            if name == "EvidenceNotes":
                return schema(items=[{"source_id": 1, "claim": "Fed holds rates", "why_it_matters": "macro"}])
            if name == "FollowupDecision":
                return schema(need_more=False, followup_queries=[], missing_angles=[],
                              evidence_confidence="medium", refusal_reason="")
            return schema.model_validate({})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _NoTickerLLM())
    monkeypatch.setattr(gmod, "web_search", _fake_web_search)
    monkeypatch.setattr(gmod, "fetch_market_snapshot", _fake_market_snapshot)
    monkeypatch.setattr(gmod, "fetch_a_share_snapshot", _fake_a_share_snapshot)

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=1, max_results_per_query=1))
    out = graph.invoke({"query": "美联储利率展望", "max_iterations": 1})

    assert out.get("market") == {}, f"expected empty market, got: {out.get('market')}"


# ─────────────────────────────────────────────────────────────
# Test 4: decide_node propagates evidence_confidence to state
# ─────────────────────────────────────────────────────────────

def test_decide_node_propagates_evidence_confidence(monkeypatch):
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model",
                        lambda cfg: _FakeLLM(evidence_confidence="high"))
    monkeypatch.setattr(gmod, "web_search", _fake_web_search)
    monkeypatch.setattr(gmod, "fetch_market_snapshot", _fake_market_snapshot)
    monkeypatch.setattr(gmod, "fetch_a_share_snapshot", _fake_a_share_snapshot)

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=1, max_results_per_query=1))
    out = graph.invoke({"query": "研究NVDA", "max_iterations": 1})

    assert out.get("evidence_confidence") == "high"


def test_route_insufficient_terminates_without_loop(monkeypatch):
    """With evidence_confidence='insufficient', graph must not loop back to search_web."""
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    search_call_count = []

    def counting_search(query, max_results=5, timeout_s=25):
        search_call_count.append(1)
        return _fake_web_search(query, max_results, timeout_s)

    # need_more=True but confidence=insufficient: should NOT loop
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model",
                        lambda cfg: _FakeLLM(evidence_confidence="insufficient", need_more=True))
    monkeypatch.setattr(gmod, "web_search", counting_search)
    monkeypatch.setattr(gmod, "fetch_market_snapshot", _fake_market_snapshot)
    monkeypatch.setattr(gmod, "fetch_a_share_snapshot", _fake_a_share_snapshot)

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=3, max_results_per_query=1))
    out = graph.invoke({"query": "研究NVDA", "max_iterations": 3})

    # search_web should have been called exactly once (initial pass only, no looping)
    assert len(search_call_count) == 1, (
        f"Expected 1 search call, got {len(search_call_count)} — 'insufficient' route did not stop the loop"
    )
    assert out.get("final_report"), "final_report should still be produced"


# ─────────────────────────────────────────────────────────────
# Test 5: all_missing_angles accumulates across iterations
# ─────────────────────────────────────────────────────────────

def test_all_missing_angles_accumulates(monkeypatch):
    """After two decide rounds, all_missing_angles should contain entries from both."""
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    call_count = [0]

    class _IteratingLLM(_FakeLLM):
        def invoke(self, prompt):
            schema = self._schema
            self._schema = None
            if schema is None:
                if "Schema name: ResearchPlan" in prompt:
                    return _Msg(
                        '{"topic":"Test","tickers":["NVDA"],"subqueries":["q1"],'
                        '"assumptions":["a1"],"market_type":"us_equity"}'
                    )
                if "Schema name: EvidenceNotes" in prompt:
                    return _Msg('{"items":[{"source_id":1,"claim":"c","why_it_matters":"w"}]}')
                if "Schema name: FollowupDecision" in prompt:
                    call_count[0] += 1
                    if call_count[0] == 1:
                        # First decide: need more, with missing angles
                        return _Msg(
                            '{"need_more":true,"followup_queries":["valuation data"],'
                            '"missing_angles":["valuation coverage missing"],'
                            '"evidence_confidence":"low","refusal_reason":""}'
                        )
                    else:
                        # Second decide: done
                        return _Msg(
                            '{"need_more":false,"followup_queries":[],'
                            '"missing_angles":["regulation coverage missing"],'
                            '"evidence_confidence":"medium","refusal_reason":""}'
                        )
                return _Msg("final report")
            name = getattr(schema, "__name__", "")
            if name == "ResearchPlan":
                return schema(topic="Test", tickers=["NVDA"], subqueries=["q1"],
                              assumptions=["a1"], market_type="us_equity")
            if name == "EvidenceNotes":
                return schema(items=[{"source_id": 1, "claim": "c", "why_it_matters": "w"}])
            if name == "FollowupDecision":
                call_count[0] += 1
                if call_count[0] == 1:
                    return schema(need_more=True, followup_queries=["valuation data"],
                                  missing_angles=["valuation coverage missing"],
                                  evidence_confidence="low", refusal_reason="")
                return schema(need_more=False, followup_queries=[],
                              missing_angles=["regulation coverage missing"],
                              evidence_confidence="medium", refusal_reason="")
            return schema.model_validate({})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _IteratingLLM())
    monkeypatch.setattr(gmod, "web_search", _fake_web_search)
    monkeypatch.setattr(gmod, "fetch_market_snapshot", _fake_market_snapshot)
    monkeypatch.setattr(gmod, "fetch_a_share_snapshot", _fake_a_share_snapshot)

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=2, max_results_per_query=1))
    out = graph.invoke({"query": "研究NVDA", "max_iterations": 2})

    all_missing = out.get("all_missing_angles") or []
    assert any("valuation" in m for m in all_missing), f"valuation gap missing: {all_missing}"
    assert any("regulation" in m for m in all_missing), f"regulation gap missing: {all_missing}"


# ─────────────────────────────────────────────────────────────
# Test 6: original smoke test (updated for new fields)
# ─────────────────────────────────────────────────────────────

def test_graph_smoke_updated(monkeypatch):
    """Full pipeline smoke test: report, source, note, and new fields all present."""
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _FakeLLM())
    monkeypatch.setattr(gmod, "web_search", _fake_web_search)
    monkeypatch.setattr(gmod, "fetch_market_snapshot", _fake_market_snapshot)
    monkeypatch.setattr(gmod, "fetch_a_share_snapshot", _fake_a_share_snapshot)

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=1, max_results_per_query=1))
    out = graph.invoke({"query": "研究NVDA", "max_iterations": 1})

    assert out.get("final_report"), "final_report should not be empty"
    assert len(out.get("sources") or []) >= 1
    assert len(out.get("notes") or []) >= 1
    assert out.get("research_timestamp"), "research_timestamp must be set"
    assert out.get("evidence_confidence") in {"high", "medium", "low", "insufficient"}
    assert isinstance(out.get("all_missing_angles"), list)
