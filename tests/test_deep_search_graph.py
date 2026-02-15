from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Msg:
    content: str


class _FakeLLM:
    def __init__(self):
        self._schema = None

    def with_structured_output(self, schema):
        self._schema = schema
        return self

    def invoke(self, prompt: str):
        schema = self._schema
        self._schema = None
        if schema is None:
            if "Schema name: ResearchPlan" in prompt:
                return _Msg(
                    '{"topic":"NVDA research","tickers":[],"subqueries":["NVDA catalysts next 12 months"],"assumptions":["demand holds"]}'
                )
            if "Schema name: EvidenceNotes" in prompt:
                return _Msg('{"items":[{"source_id":1,"claim":"evidence","why_it_matters":"matters"}]}')
            if "Schema name: FollowupDecision" in prompt:
                return _Msg('{"need_more":false,"followup_queries":[],"missing_angles":[]}')
            return _Msg("report")

        name = getattr(schema, "__name__", str(schema))

        if name == "ResearchPlan":
            return schema(
                topic="NVDA 研究",
                tickers=[],
                subqueries=["NVDA catalysts next 12 months"],
                assumptions=["需求持续"],
            )

        if name == "EvidenceNotes":
            return schema(items=[{"source_id": 1, "claim": "证据", "why_it_matters": "重要"}])

        if name == "FollowupDecision":
            return schema(need_more=False, followup_queries=[], missing_angles=[])

        return schema.model_validate({})


def test_graph_runs_without_network(monkeypatch):
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod
    from stock_agent.tools.web_search import WebDocument

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _FakeLLM())
    monkeypatch.setattr(
        gmod,
        "web_search",
        lambda q, max_results=5, timeout_s=25: [WebDocument(title="t", url="http://x", content="c")],
    )

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=1, max_results_per_query=1))
    out = graph.invoke({"query": "研究NVDA", "max_iterations": 1})

    assert out["final_report"] == "report"
    assert len(out.get("sources") or []) == 1
    assert len(out.get("notes") or []) == 1
