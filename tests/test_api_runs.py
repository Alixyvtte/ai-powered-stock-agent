from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stock_agent.event_adapter import (
    build_run_completed_event,
    build_run_failed_event,
    build_run_started_event,
    build_step_event,
)
from stock_agent.web.app import create_app
from stock_agent.web.runs import InMemoryRunStore


def test_create_run_returns_run_id_and_queued_status() -> None:
    client = TestClient(create_app(run_store=InMemoryRunStore()))

    response = client.post("/api/runs", json={"query": "Analyze Microsoft cloud growth"})

    assert response.status_code == 201
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert data["run_id"]
    assert data["status"] == "queued"


@pytest.mark.parametrize("query", ["", "   "])
def test_create_run_rejects_empty_queries(query: str) -> None:
    client = TestClient(create_app(run_store=InMemoryRunStore()))

    response = client.post("/api/runs", json={"query": query})

    assert response.status_code == 400
    assert response.json() == {"detail": "Query must not be empty."}


def test_create_run_accepts_mode_preset() -> None:
    store = InMemoryRunStore()
    client = TestClient(create_app(run_store=store))

    response = client.post("/api/runs", json={"query": "Analyze NVDA", "mode": "fast"})

    assert response.status_code == 201
    run_id = response.json()["run_id"]
    run = store.get_run(run_id)
    assert run is not None and run.mode == "fast"


def test_create_run_invalid_mode_falls_back_to_standard() -> None:
    store = InMemoryRunStore()
    client = TestClient(create_app(run_store=store))

    response = client.post("/api/runs", json={"query": "Analyze NVDA", "mode": "bogus"})

    assert response.status_code == 201
    run = store.get_run(response.json()["run_id"])
    assert run is not None and run.mode == "standard"


def test_create_run_rejects_second_active_run() -> None:
    client = TestClient(create_app(run_store=InMemoryRunStore()))

    first_response = client.post("/api/runs", json={"query": "First query"})
    second_response = client.post("/api/runs", json={"query": "Second query"})

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert second_response.json() == {"detail": "Another run is already active."}


def test_get_run_snapshot_returns_latest_run_state() -> None:
    store = InMemoryRunStore()
    client = TestClient(create_app(run_store=store))

    create_response = client.post("/api/runs", json={"query": "Analyze Tesla margins"})
    run_id = create_response.json()["run_id"]
    store.record_progress(
        run_id,
        latest_node="market",
        snapshot={"market": {"TSLA": {"price": 245.1}}},
        summaries={"market": {"ticker_count": 1}},
    )

    response = client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert data["run_id"] == run_id
    assert data["query"] == "Analyze Tesla margins"
    assert data["status"] == "running"
    assert data["created_at"]
    assert data["updated_at"]
    assert data["started_at"]
    assert data["finished_at"] is None
    assert data["latest_node"] == "market"
    assert data["snapshot"] == {"market": {"TSLA": {"price": 245.1}}}
    assert data["summaries"] == {"market": {"ticker_count": 1}}
    assert data["final_report"] is None
    assert data["final_report_html"] is None
    assert data["evidence_confidence"] is None
    assert data["followup_history"] == []
    assert data["market_highlights"] == []
    assert data["error"] is None


def test_get_run_snapshot_returns_completed_state() -> None:
    store = InMemoryRunStore()
    client = TestClient(create_app(run_store=store))

    create_response = client.post("/api/runs", json={"query": "Analyze Microsoft cloud growth"})
    run_id = create_response.json()["run_id"]

    run_started = build_run_started_event(
        "Analyze Microsoft cloud growth",
        {"query": "Analyze Microsoft cloud growth"},
        timestamp="2026-04-04T01:00:00+00:00",
    )
    market_event = build_step_event(
        "market",
        run_started["snapshot"],
        {
            "plan": {"tickers": ["MSFT"]},
            "market": {
                "MSFT": {
                    "yfinance": {
                        "ticker": "MSFT",
                        "currency": "USD",
                        "price": 428.2,
                        "market_cap": 3180000000000,
                        "forward_pe": 31.4,
                    }
                }
            },
        },
        timestamp="2026-04-04T01:00:01+00:00",
    )
    assert market_event is not None
    decide_event = build_step_event(
        "decide",
        market_event["snapshot"],
        {
            "need_more": False,
            "followup_queries": ["Azure pricing checks", "Copilot attach rate"],
            "evidence_confidence": "low",
        },
        timestamp="2026-04-04T01:00:02+00:00",
    )
    assert decide_event is not None
    write_report_event = build_step_event(
        "write_report",
        decide_event["snapshot"],
        {"final_report": "# Completed report\n\nUse **caution**."},
        timestamp="2026-04-04T01:00:03+00:00",
    )
    assert write_report_event is not None

    for event in [
        run_started,
        market_event,
        decide_event,
        write_report_event,
        build_run_completed_event(write_report_event["snapshot"], timestamp="2026-04-04T01:00:04+00:00"),
    ]:
        store.append_event(run_id, event)

    response = client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["finished_at"]
    assert data["latest_node"] == "write_report"
    assert data["snapshot"] == write_report_event["snapshot"]
    assert data["summaries"] == {
        "market": {
            "ticker_count": 1,
            "covered_ticker_count": 1,
            "tickers_with_price": 1,
            "tickers_with_market_cap": 1,
        },
        "decide": {
            "need_more": False,
            "followup_count": 2,
            "evidence_confidence": "low",
        },
        "write_report": {"report_length": 36},
    }
    assert data["final_report"] == "# Completed report\n\nUse **caution**."
    assert "<h1>Completed report</h1>" in data["final_report_html"]
    assert "<strong>caution</strong>" in data["final_report_html"]
    assert data["evidence_confidence"] == "low"
    assert data["followup_history"] == ["Azure pricing checks", "Copilot attach rate"]
    assert data["market_highlights"] == [
        {
            "ticker": "MSFT",
            "source": "yfinance",
            "currency": "USD",
            "price": 428.2,
            "market_cap": 3180000000000,
            "trailing_pe": None,
            "forward_pe": 31.4,
            "dividend_yield": None,
        }
    ]
    assert data["error"] is None


def test_get_run_snapshot_returns_failed_state_without_rendered_report_html() -> None:
    store = InMemoryRunStore()
    client = TestClient(create_app(run_store=store))

    create_response = client.post("/api/runs", json={"query": "Analyze Amazon cloud margins"})
    run_id = create_response.json()["run_id"]

    run_started = build_run_started_event(
        "Analyze Amazon cloud margins",
        {"query": "Analyze Amazon cloud margins"},
        timestamp="2026-04-04T02:00:00+00:00",
    )
    decide_event = build_step_event(
        "decide",
        run_started["snapshot"],
        {
            "need_more": False,
            "followup_queries": ["AWS competitive pricing"],
            "evidence_confidence": "insufficient",
        },
        timestamp="2026-04-04T02:00:01+00:00",
    )
    assert decide_event is not None
    failed_event = build_run_failed_event(
        "search provider failed",
        decide_event["snapshot"],
        node="decide",
        timestamp="2026-04-04T02:00:02+00:00",
    )

    store.append_event(run_id, run_started)
    store.append_event(run_id, decide_event)
    store.append_event(run_id, failed_event)

    response = client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["finished_at"]
    assert data["latest_node"] == "decide"
    assert data["final_report"] is None
    assert data["final_report_html"] is None
    assert data["evidence_confidence"] == "insufficient"
    assert data["followup_history"] == ["AWS competitive pricing"]
    assert data["market_highlights"] == []
    assert data["error"] == "search provider failed"


def test_snapshot_surfaces_thesis_and_verification_for_ui() -> None:
    """The snapshot the UI renders (verdict card, self-check, evidence panel)
    must carry thesis / verification / sources / notes."""
    store = InMemoryRunStore()
    client = TestClient(create_app(run_store=store))
    run = store.create_run("Analyze NVDA")
    store.complete_run(
        run.run_id,
        final_report="Memo body [S1]",
        snapshot={
            "query": "Analyze NVDA",
            "thesis": {"verdict": "bullish", "conviction": "medium", "bull_points": ["AI demand [S1]"]},
            "verification": {"citations": 1, "invalid_citations": 0, "passed": True},
            "sources": [{"id": 1, "title": "Reuters", "url": "https://reuters.com/x", "fetched": True}],
            "notes": [{"source_id": 1, "claim": "Revenue grew", "why_it_matters": "growth"}],
            "final_report": "Memo body [S1]",
        },
    )

    data = client.get(f"/api/runs/{run.run_id}").json()
    snap = data["snapshot"]
    assert snap["thesis"]["verdict"] == "bullish"
    assert snap["verification"]["passed"] is True
    assert snap["sources"][0]["url"] == "https://reuters.com/x"
    assert snap["notes"][0]["claim"] == "Revenue grew"
    assert data["duration_s"] is not None


def test_get_run_snapshot_returns_404_for_unknown_run() -> None:
    client = TestClient(create_app(run_store=InMemoryRunStore()))

    response = client.get("/api/runs/not-a-real-id")

    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found."}
