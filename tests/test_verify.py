from __future__ import annotations

import json
import re
from dataclasses import dataclass

from stock_agent.config import AgentConfig
from stock_agent.graphs.deep_search_graph import _cited_ids, _sanitize_citations


# ── deterministic citation helpers ──────────────────────────

def test_cited_ids_extracts_unique_ids():
    assert _cited_ids("foo [S1] bar [S3] baz [S1]") == {1, 3}
    assert _cited_ids("no citations here") == set()


def test_sanitize_citations_drops_invalid_markers():
    text = "Claim A [S1]. Claim B [S99]. Claim C [S2]."
    clean, removed = _sanitize_citations(text, {1, 2})
    assert removed == 1
    assert "[S99]" not in clean
    assert "[S1]" in clean and "[S2]" in clean


# ── full-graph: verify strips hallucinated citations ────────

@dataclass(frozen=True)
class _Msg:
    content: str


class _FakeLLM:
    """Report cites a non-existent source [S99]; only [S1] is real."""

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        if "Schema name: ResearchPlan" in prompt:
            return _Msg(json.dumps({
                "topic": "NVDA", "tickers": [], "subqueries": ["q1"],
                "assumptions": ["a"], "market_type": "us_equity",
            }))
        if "Schema name: EvidenceNotes" in prompt:
            m = re.search(r"\[source_id=(\d+)\]", prompt)
            sid = int(m.group(1)) if m else 1
            return _Msg(json.dumps({"items": [{
                "source_id": sid,
                "claim": "Data center revenue grew strongly year over year in the latest quarter.",
                "why_it_matters": "Signals durable demand and supports the growth thesis.",
            }]}))
        if "Schema name: FollowupDecision" in prompt:
            return _Msg(json.dumps({
                "need_more": False, "followup_queries": [], "missing_angles": [],
                "evidence_confidence": "high", "refusal_reason": "",
            }))
        if "Schema name: InvestmentThesis" in prompt:
            return _Msg(json.dumps({
                "verdict": "bullish", "conviction": "medium",
                "bull_points": ["demand [S1]"], "bear_points": ["valuation"],
                "valuation_view": "rich", "price_view": "below target",
            }))
        if "Schema name: VerificationResult" in prompt:
            return _Msg(json.dumps({"passed": True, "issues": []}))
        # write_node: cite a real source [S1] and a hallucinated one [S99]
        return _Msg("Executive Summary. Revenue strong [S1]. Fabricated claim [S99].")


def _fake_doc(i: int):
    from stock_agent.tools.web_search import WebDocument

    return WebDocument(title=f"S{i}", url=f"https://example.com/{i}", content="snippet about growth and margins.")


def test_verify_strips_hallucinated_citation(monkeypatch):
    from stock_agent.graphs import deep_search_graph as gmod

    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _FakeLLM())
    monkeypatch.setattr(gmod, "web_search", lambda q, max_results=5, timeout_s=25: [_fake_doc(1)])

    graph = gmod.build_deep_search_graph(
        AgentConfig(max_iterations=1, max_results_per_query=1, fetch_top_n=0)
    )
    out = graph.invoke({"query": "Research NVDA", "max_iterations": 1})

    report = out.get("final_report") or ""
    assert "[S1]" in report, "valid citation kept"
    assert "[S99]" not in report, "hallucinated citation removed by verify"
    verification = out.get("verification") or {}
    assert verification.get("invalid_citations", 0) >= 1
