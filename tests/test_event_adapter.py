from __future__ import annotations

from typing import Any, Iterator

from stock_agent.agent import DeepSearchAgent
from stock_agent.config import AgentConfig
from stock_agent.event_adapter import build_step_event


def test_build_step_event_produces_expected_summary_shapes() -> None:
    initial = {"query": "Research NVDA", "max_iterations": 2}

    plan_event = build_step_event(
        "plan",
        initial,
        {
            "plan": {
                "topic": "NVDA research",
                "tickers": ["NVDA", "AMD"],
                "subqueries": ["business", "valuation", "risks"],
            },
            "subqueries": ["business", "valuation", "risks"],
        },
        timestamp="2026-04-04T00:00:00+00:00",
    )
    assert plan_event is not None
    assert plan_event["summary"] == {
        "topic": "NVDA research",
        "ticker_count": 2,
        "subquery_count": 3,
    }
    assert plan_event["warnings"] == []

    market_event = build_step_event(
        "market",
        plan_event["snapshot"],
        {
            "market": {
                "NVDA": {
                    "yfinance": {"ticker": "NVDA", "price": 900.0, "market_cap": 2200000000000},
                    "akshare": {"ticker": "NVDA", "error": "not an a-share"},
                },
                "AMD": {
                    "yfinance": {"ticker": "AMD", "error": "provider failed"},
                },
            }
        },
        timestamp="2026-04-04T00:00:01+00:00",
    )
    assert market_event is not None
    assert market_event["summary"] == {
        "ticker_count": 2,
        "covered_ticker_count": 1,
        "tickers_with_price": 1,
        "tickers_with_market_cap": 1,
    }
    assert market_event["warnings"] == []

    search_event = build_step_event(
        "search_web",
        market_event["snapshot"],
        {"sources": [{"id": 1}, {"id": 2}]},
        timestamp="2026-04-04T00:00:02+00:00",
    )
    assert search_event is not None
    assert search_event["summary"] == {
        "total_sources": 2,
        "new_sources": 2,
    }
    assert search_event["warnings"] == []

    extract_event = build_step_event(
        "extract",
        search_event["snapshot"],
        {"notes": [{"source_id": 1}, {"source_id": 2}, {"source_id": 2}]},
        timestamp="2026-04-04T00:00:03+00:00",
    )
    assert extract_event is not None
    assert extract_event["summary"] == {
        "total_notes": 3,
        "new_notes": 3,
    }
    assert extract_event["warnings"] == []

    decide_event = build_step_event(
        "decide",
        extract_event["snapshot"],
        {
            "need_more": True,
            "followup_queries": ["NVDA supply chain checks"],
            "evidence_confidence": "medium",
        },
        timestamp="2026-04-04T00:00:04+00:00",
    )
    assert decide_event is not None
    assert decide_event["summary"] == {
        "need_more": True,
        "followup_count": 1,
        "evidence_confidence": "medium",
    }
    assert decide_event["warnings"] == []

    write_event = build_step_event(
        "write_report",
        decide_event["snapshot"],
        {"final_report": "Final memo"},
        timestamp="2026-04-04T00:00:05+00:00",
    )
    assert write_event is not None
    assert write_event["summary"] == {"report_length": 10}
    assert write_event["warnings"] == []


def test_build_step_event_adds_high_signal_warnings() -> None:
    market_event = build_step_event(
        "market",
        {
            "query": "Research TSLA",
            "plan": {"tickers": ["TSLA", "F"]},
        },
        {
            "market": {
                "TSLA": {"yfinance": {"ticker": "TSLA", "error": "down"}},
                "F": {"yfinance": {"ticker": "F", "error": "down"}},
            }
        },
        timestamp="2026-04-04T00:01:00+00:00",
    )
    assert market_event is not None
    assert market_event["warnings"] == [
        {
            "code": "empty_market_data",
            "severity": "warning",
            "message": "No usable market snapshot was returned for the planned tickers.",
        }
    ]

    extract_event = build_step_event(
        "extract",
        {"notes": [{"source_id": 1}]},
        {"notes": [{"source_id": 1}]},
        timestamp="2026-04-04T00:01:01+00:00",
    )
    assert extract_event is not None
    assert extract_event["warnings"] == [
        {
            "code": "no_new_notes",
            "severity": "warning",
            "message": "No new evidence notes were extracted from the latest source batch.",
        }
    ]

    low_confidence_event = build_step_event(
        "decide",
        {},
        {"need_more": False, "followup_queries": [], "evidence_confidence": "low"},
        timestamp="2026-04-04T00:01:02+00:00",
    )
    assert low_confidence_event is not None
    assert low_confidence_event["warnings"] == [
        {
            "code": "low_evidence_confidence",
            "severity": "warning",
            "message": "Evidence confidence is low, so the resulting memo should be treated cautiously.",
        }
    ]

    insufficient_event = build_step_event(
        "decide",
        {},
        {"need_more": False, "followup_queries": [], "evidence_confidence": "insufficient"},
        timestamp="2026-04-04T00:01:03+00:00",
    )
    assert insufficient_event is not None
    assert insufficient_event["warnings"] == [
        {
            "code": "low_evidence_confidence",
            "severity": "warning",
            "message": "Evidence confidence is low, so the resulting memo should be treated cautiously.",
        }
    ]


def test_stream_events_emits_ordered_events_and_filters_unknown_updates(monkeypatch) -> None:
    agent = object.__new__(DeepSearchAgent)
    agent._config = AgentConfig(max_iterations=2)

    def fake_stream(query: str) -> Iterator[tuple[str, dict[str, Any]]]:
        assert query == "Research NVDA"
        yield "plan", {
            "plan": {
                "topic": "NVDA research",
                "tickers": ["NVDA"],
                "subqueries": ["business", "valuation"],
            },
            "subqueries": ["business", "valuation"],
        }
        yield "event", {"_raw": {"provider": "noise"}}
        yield "search_web", {"sources": [{"id": 1}, {"id": 2}]}
        yield "extract", {"notes": [{"source_id": 1}]}
        yield "decide", {"need_more": True, "followup_queries": ["NVDA supply"], "evidence_confidence": "low"}
        yield "mystery", {"ignored": True}
        yield "search_web", {"sources": [{"id": 1}, {"id": 2}, {"id": 3}]}
        yield "extract", {"notes": [{"source_id": 1}, {"source_id": 3}]}
        yield "decide", {"need_more": False, "followup_queries": [], "evidence_confidence": "high"}
        yield "write_report", {"final_report": "Memo body"}

    agent.stream = fake_stream
    monkeypatch.setattr("stock_agent.event_adapter._utc_now_iso", lambda: "2026-04-04T00:00:00+00:00")

    events = list(agent.stream_events("Research NVDA"))

    assert [event["type"] for event in events] == [
        "run_started",
        "step_completed",
        "step_completed",
        "step_completed",
        "step_completed",
        "step_completed",
        "step_completed",
        "step_completed",
        "step_completed",
        "run_completed",
    ]
    assert [event["node"] for event in events if event["type"] == "step_completed"] == [
        "plan",
        "search_web",
        "extract",
        "decide",
        "search_web",
        "extract",
        "decide",
        "write_report",
    ]
    assert events[0]["query"] == "Research NVDA"
    assert events[4]["warnings"][0]["code"] == "low_evidence_confidence"
    assert events[-1]["final_report"] == "Memo body"
    assert events[-1]["snapshot"]["final_report"] == "Memo body"


def test_stream_events_emits_run_failed_with_last_snapshot(monkeypatch) -> None:
    agent = object.__new__(DeepSearchAgent)
    agent._config = AgentConfig(max_iterations=1)

    def failing_stream(query: str) -> Iterator[tuple[str, dict[str, Any]]]:
        assert query == "Research AMD"
        yield "plan", {
            "plan": {
                "topic": "AMD research",
                "tickers": ["AMD"],
                "subqueries": ["valuation"],
            },
            "subqueries": ["valuation"],
        }
        raise RuntimeError("simulated stream failure")

    agent.stream = failing_stream
    monkeypatch.setattr("stock_agent.event_adapter._utc_now_iso", lambda: "2026-04-04T00:05:00+00:00")

    events = list(agent.stream_events("Research AMD"))

    assert [event["type"] for event in events] == [
        "run_started",
        "step_completed",
        "run_failed",
    ]
    failed_event = events[-1]
    assert failed_event["error"] == "simulated stream failure"
    assert failed_event["node"] == "plan"
    assert failed_event["snapshot"]["plan"]["topic"] == "AMD research"
