from __future__ import annotations

import json
import re
from dataclasses import dataclass

from stock_agent.config import AgentConfig
from stock_agent.tools.content_fetch import _extract_main_text


SAMPLE_HTML = """
<html><head><title>NVDA</title><style>.x{}</style></head>
<body>
  <nav>home about contact</nav>
  <header>site header noise</header>
  <article>
    <h1>NVIDIA Q4 Results</h1>
    <p>NVIDIA reported record data-center revenue of twenty-two billion dollars this quarter.</p>
    <p>Management guided to continued strength driven by AI training demand into next year.</p>
    <script>var tracking = 1;</script>
  </article>
  <footer>copyright 2026 all rights reserved</footer>
</body></html>
"""


def test_extract_main_text_pulls_body_and_drops_chrome():
    text = _extract_main_text(SAMPLE_HTML, max_chars=5000)
    assert "record data-center revenue" in text
    assert "continued strength" in text
    assert "tracking" not in text          # <script> stripped
    assert "site header noise" not in text  # <header> stripped


def test_extract_main_text_truncates():
    big = "<article>" + "".join(f"<p>{'word ' * 20}</p>" for _ in range(50)) + "</article>"
    assert len(_extract_main_text(big, max_chars=200)) <= 200


def test_extract_main_text_handles_empty():
    assert _extract_main_text("", max_chars=100) == ""


# ── full-graph behavior (LLM / search / fetch mocked) ───────────

@dataclass(frozen=True)
class _Msg:
    content: str


class _FakeLLM:
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
                "why_it_matters": "It signals durable demand and supports the growth thesis.",
            }]}))
        if "Schema name: FollowupDecision" in prompt:
            return _Msg(json.dumps({
                "need_more": False, "followup_queries": [], "missing_angles": [],
                "evidence_confidence": "high", "refusal_reason": "",
            }))
        return _Msg("final report body")


def _fake_doc(i: int):
    from stock_agent.tools.web_search import WebDocument

    return WebDocument(
        title=f"Source {i}",
        url=f"https://example.com/{i}",
        content=f"short snippet {i} about revenue growth and gross margin improvement.",
    )


def test_fetch_content_enriches_in_full_graph(monkeypatch):
    from stock_agent.graphs import deep_search_graph as gmod

    long_text = "FULL ARTICLE BODY: " + ("detailed financial analysis sentence. " * 30)
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _FakeLLM())
    monkeypatch.setattr(gmod, "web_search", lambda q, max_results=5, timeout_s=25: [_fake_doc(1), _fake_doc(2)])
    fetch_calls = []
    monkeypatch.setattr(
        gmod, "fetch_readable_text",
        lambda url, timeout_s=10, max_chars=5000: fetch_calls.append(url) or long_text,
    )

    graph = gmod.build_deep_search_graph(
        AgentConfig(max_iterations=1, max_results_per_query=2, fetch_top_n=2)
    )
    out = graph.invoke({"query": "Research NVDA", "max_iterations": 1})

    sources = out.get("sources") or []
    assert len(fetch_calls) >= 1, "fetch_content should fetch top sources"
    assert any(str(s.get("content", "")).startswith("FULL ARTICLE BODY") for s in sources)
    assert any(s.get("fetched") for s in sources)
    assert out.get("final_report")  # pipeline still completes


def test_fetch_content_noop_when_disabled(monkeypatch):
    from stock_agent.graphs import deep_search_graph as gmod

    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _FakeLLM())
    monkeypatch.setattr(gmod, "web_search", lambda q, max_results=5, timeout_s=25: [_fake_doc(1)])
    fetch_calls = []
    monkeypatch.setattr(
        gmod, "fetch_readable_text",
        lambda url, timeout_s=10, max_chars=5000: fetch_calls.append(url) or "x",
    )

    graph = gmod.build_deep_search_graph(
        AgentConfig(max_iterations=1, max_results_per_query=1, fetch_top_n=0)
    )
    out = graph.invoke({"query": "Research NVDA", "max_iterations": 1})

    assert fetch_calls == [], "no network fetch when fetch_top_n=0"
    assert not any(s.get("fetched") for s in (out.get("sources") or []))
