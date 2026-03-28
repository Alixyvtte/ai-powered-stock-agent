from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _Msg:
    content: str


class _ScriptedLLM:
    def __init__(self, *, evidence_fn=None, followup_responses=None, report="report"):
        self._schema = None
        self._evidence_fn = evidence_fn or (lambda prompt: {"items": []})
        self._followup_responses = list(followup_responses or [{"need_more": False, "followup_queries": [], "missing_angles": []}])
        self.report = report

    def with_structured_output(self, schema):
        self._schema = schema
        return self

    def invoke(self, prompt: str):
        schema = self._schema
        self._schema = None
        if schema is not None:
            name = getattr(schema, "__name__", str(schema))
            if name == "ResearchPlan":
                return schema(
                    topic="NVDA research",
                    tickers=[],
                    subqueries=["NVDA catalysts next 12 months"],
                    assumptions=["Demand remains resilient."],
                )
            if name == "EvidenceNotes":
                return schema.model_validate(self._evidence_fn(prompt))
            if name == "FollowupDecision":
                return schema.model_validate(self._next_followup())
            return schema.model_validate({})

        if "Schema name: ResearchPlan" in prompt:
            return _Msg(
                json.dumps(
                    {
                        "topic": "NVDA research",
                        "tickers": [],
                        "subqueries": ["NVDA catalysts next 12 months"],
                        "assumptions": ["Demand remains resilient."],
                    }
                )
            )
        if "Schema name: EvidenceNotes" in prompt:
            return _Msg(json.dumps(self._evidence_fn(prompt)))
        if "Schema name: FollowupDecision" in prompt:
            return _Msg(json.dumps(self._next_followup()))
        return _Msg(self.report)

    def _next_followup(self):
        if self._followup_responses:
            return self._followup_responses.pop(0)
        return {"need_more": False, "followup_queries": [], "missing_angles": []}


def _source_id_from_prompt(prompt: str) -> int:
    match = re.search(r"\[source_id=(\d+)\]", prompt)
    if not match:
        raise AssertionError(f"source_id missing from prompt: {prompt}")
    return int(match.group(1))


def _doc(i: int):
    from stock_agent.tools.web_search import WebDocument

    return WebDocument(
        title=f"Source {i}",
        url=f"https://example.com/{i}",
        content=f"Source {i} says revenue grew 25 percent year over year and gross margin improved.",
    )


def test_graph_runs_without_network(monkeypatch):
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(
        gmod,
        "get_chat_model",
        lambda cfg: _ScriptedLLM(
            evidence_fn=lambda prompt: {
                "items": [
                    {
                        "source_id": _source_id_from_prompt(prompt),
                        "claim": "Revenue grew 25 percent year over year in the reported period.",
                        "why_it_matters": "This supports near-term demand strength and earnings momentum.",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(gmod, "web_search", lambda q, max_results=5, timeout_s=25: [_doc(1)])

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=1, max_results_per_query=1))
    out = graph.invoke({"query": "Research NVDA", "max_iterations": 1})

    assert out["final_report"] == "report"
    assert len(out.get("sources") or []) == 1
    assert len(out.get("notes") or []) == 1
    assert out.get("processed_source_ids") == [1]


def test_extract_marks_zero_note_sources_processed_and_advances_to_later_sources(monkeypatch):
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    first_pass = [_doc(i) for i in range(1, 7)]
    second_pass = [_doc(i) for i in range(7, 13)]
    searches = [first_pass, second_pass]

    def fake_search(query, max_results=5, timeout_s=25):
        return searches.pop(0)

    def evidence_fn(prompt: str):
        source_id = _source_id_from_prompt(prompt)
        if source_id == 7:
            return {
                "items": [
                    {
                        "source_id": 7,
                        "claim": "Management said Blackwell supply constraints should ease later this year.",
                        "why_it_matters": "That would support shipment growth and improve near-term revenue visibility.",
                    }
                ]
            }
        return {"items": []}

    llm = _ScriptedLLM(
        evidence_fn=evidence_fn,
        followup_responses=[
            {"need_more": True, "followup_queries": ["NVDA follow-up"], "missing_angles": ["earnings color"]},
            {"need_more": False, "followup_queries": [], "missing_angles": []},
        ],
    )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: llm)
    monkeypatch.setattr(gmod, "web_search", fake_search)

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=2, max_results_per_query=6))
    out = graph.invoke({"query": "Research NVDA", "max_iterations": 2})

    assert [note["source_id"] for note in out.get("notes") or []] == [7]
    assert out.get("processed_source_ids") == list(range(1, 13))


def test_extract_enforces_two_notes_per_source(monkeypatch):
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    def evidence_fn(prompt: str):
        source_id = _source_id_from_prompt(prompt)
        return {
            "items": [
                {
                    "source_id": source_id,
                    "claim": "Revenue grew 25 percent year over year in the reported quarter.",
                    "why_it_matters": "This indicates strong demand and supports the growth case.",
                },
                {
                    "source_id": source_id,
                    "claim": "Gross margin expanded sequentially as the product mix improved.",
                    "why_it_matters": "Margin expansion can lift earnings leverage and free cash flow.",
                },
                {
                    "source_id": source_id,
                    "claim": "Management guided to another strong quarter driven by AI demand.",
                    "why_it_matters": "Forward guidance is a direct catalyst for estimate revisions.",
                },
            ]
        }

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _ScriptedLLM(evidence_fn=evidence_fn))
    monkeypatch.setattr(gmod, "web_search", lambda q, max_results=5, timeout_s=25: [_doc(1)])

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=1, max_results_per_query=1))
    out = graph.invoke({"query": "Research NVDA", "max_iterations": 1})

    notes = out.get("notes") or []
    assert len(notes) == 2
    assert {note["source_id"] for note in notes} == {1}


def test_extract_filters_mismatched_and_duplicate_claims(monkeypatch):
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    def evidence_fn(prompt: str):
        source_id = _source_id_from_prompt(prompt)
        return {
            "items": [
                {
                    "source_id": 999,
                    "claim": "A mismatched note should never be accepted into the state.",
                    "why_it_matters": "The citation would point to the wrong source.",
                },
                {
                    "source_id": source_id,
                    "claim": "Data center revenue reached a record level in the latest quarter.",
                    "why_it_matters": "That reinforces the core growth engine for the bull case.",
                },
                {
                    "source_id": source_id,
                    "claim": "Data center revenue reached a record level in the latest quarter.",
                    "why_it_matters": "Duplicate notes should be dropped during validation.",
                },
            ]
        }

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: _ScriptedLLM(evidence_fn=evidence_fn))
    monkeypatch.setattr(gmod, "web_search", lambda q, max_results=5, timeout_s=25: [_doc(1)])

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=1, max_results_per_query=1))
    out = graph.invoke({"query": "Research NVDA", "max_iterations": 1})

    notes = out.get("notes") or []
    assert len(notes) == 1
    assert notes[0]["source_id"] == 1


def test_extract_failure_does_not_block_other_sources_or_retry_failed_source(monkeypatch):
    from stock_agent.config import AgentConfig
    from stock_agent.graphs import deep_search_graph as gmod

    prompt_counts = {1: 0, 2: 0, 3: 0}
    searches = [[_doc(1), _doc(2)], [_doc(3)]]

    def fake_search(query, max_results=5, timeout_s=25):
        return searches.pop(0)

    def evidence_fn(prompt: str):
        source_id = _source_id_from_prompt(prompt)
        prompt_counts[source_id] += 1
        if source_id == 1:
            raise RuntimeError("simulated extraction failure")
        return {
            "items": [
                {
                    "source_id": source_id,
                    "claim": f"Source {source_id} contains a concrete operating update with timing detail.",
                    "why_it_matters": "That operating update can change investor expectations for upcoming results.",
                }
            ]
        }

    llm = _ScriptedLLM(
        evidence_fn=evidence_fn,
        followup_responses=[
            {"need_more": True, "followup_queries": ["NVDA follow-up"], "missing_angles": ["channel checks"]},
            {"need_more": False, "followup_queries": [], "missing_angles": []},
        ],
    )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gmod, "get_chat_model", lambda cfg: llm)
    monkeypatch.setattr(gmod, "web_search", fake_search)

    graph = gmod.build_deep_search_graph(AgentConfig(max_iterations=2, max_results_per_query=3))
    out = graph.invoke({"query": "Research NVDA", "max_iterations": 2})

    assert [note["source_id"] for note in out.get("notes") or []] == [2, 3]
    assert prompt_counts == {1: 1, 2: 1, 3: 1}
    assert out.get("processed_source_ids") == [1, 2, 3]
