from __future__ import annotations

import json
from typing import Any, Iterator

from fastapi.testclient import TestClient

from stock_agent.event_adapter import (
    build_run_completed_event,
    build_run_started_event,
    build_step_event,
)
from stock_agent.web.app import create_app
from stock_agent.web.runs import InMemoryRunStore


def _collect_sse_events(response) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_type: str | None = None
    data_lines: list[str] = []

    for raw_line in response.iter_lines():
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if line == "":
            if event_type is not None:
                payload = json.loads("\n".join(data_lines))
                assert payload["type"] == event_type
                events.append(payload)
            event_type = None
            data_lines = []
            continue

        if line.startswith("event:"):
            event_type = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())

    return events


def _build_success_events(query: str) -> list[dict[str, Any]]:
    snapshot = {"query": query, "max_iterations": 2}
    run_started = build_run_started_event(
        query,
        snapshot,
        timestamp="2026-04-04T00:00:00+00:00",
    )
    plan_event = build_step_event(
        "plan",
        snapshot,
        {
            "plan": {
                "topic": "NVDA research",
                "tickers": ["NVDA"],
                "subqueries": ["business", "valuation"],
            },
            "subqueries": ["business", "valuation"],
        },
        timestamp="2026-04-04T00:00:01+00:00",
    )
    assert plan_event is not None

    write_report_event = build_step_event(
        "write_report",
        plan_event["snapshot"],
        {"final_report": "# Memo\n\nMemo body"},
        timestamp="2026-04-04T00:00:02+00:00",
    )
    assert write_report_event is not None

    run_completed = build_run_completed_event(
        write_report_event["snapshot"],
        timestamp="2026-04-04T00:00:03+00:00",
    )
    return [run_started, plan_event, write_report_event, run_completed]


class FakeAgent:
    def __init__(self, events: list[dict[str, Any]], *, error: Exception | None = None):
        self._events = events
        self._error = error

    def stream_events(self, query: str) -> Iterator[dict[str, Any]]:
        assert query
        for event in self._events:
            yield event
        if self._error is not None:
            raise self._error


def test_sse_stream_executes_run_and_persists_completed_snapshot() -> None:
    store = InMemoryRunStore()
    query = "Research NVDA"
    app = create_app(
        run_store=store,
        agent_factory=lambda: FakeAgent(_build_success_events(query)),
    )
    client = TestClient(app)

    create_response = client.post("/api/runs", json={"query": query})
    run_id = create_response.json()["run_id"]

    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _collect_sse_events(response)

    assert [event["type"] for event in events] == [
        "run_started",
        "step_completed",
        "step_completed",
        "run_completed",
    ]
    assert [event["node"] for event in events if event["type"] == "step_completed"] == [
        "plan",
        "write_report",
    ]
    assert events[-1]["final_report"] == "# Memo\n\nMemo body"
    assert "<h1>Memo</h1>" in events[-1]["final_report_html"]
    assert "<p>Memo body</p>" in events[-1]["final_report_html"]

    snapshot_response = client.get(f"/api/runs/{run_id}")
    data = snapshot_response.json()
    assert data["status"] == "completed"
    assert data["latest_node"] == "write_report"
    assert data["final_report"] == "# Memo\n\nMemo body"
    assert "<h1>Memo</h1>" in data["final_report_html"]
    assert data["error"] is None
    assert data["snapshot"]["final_report"] == "# Memo\n\nMemo body"
    assert data["summaries"]["plan"] == {
        "topic": "NVDA research",
        "ticker_count": 1,
        "subquery_count": 2,
    }
    assert data["summaries"]["write_report"] == {"report_length": 17}


def test_sse_stream_emits_run_failed_and_releases_active_slot() -> None:
    store = InMemoryRunStore()
    query = "Research AMD"
    initial_snapshot = {"query": query, "max_iterations": 1}
    run_started = build_run_started_event(
        query,
        initial_snapshot,
        timestamp="2026-04-04T00:05:00+00:00",
    )
    plan_event = build_step_event(
        "plan",
        initial_snapshot,
        {
            "plan": {
                "topic": "AMD research",
                "tickers": ["AMD"],
                "subqueries": ["valuation"],
            },
            "subqueries": ["valuation"],
        },
        timestamp="2026-04-04T00:05:01+00:00",
    )
    assert plan_event is not None

    app = create_app(
        run_store=store,
        agent_factory=lambda: FakeAgent(
            [run_started, plan_event],
            error=RuntimeError("simulated stream failure"),
        ),
    )
    client = TestClient(app)

    create_response = client.post("/api/runs", json={"query": query})
    run_id = create_response.json()["run_id"]

    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.status_code == 200
        events = _collect_sse_events(response)

    assert [event["type"] for event in events] == [
        "run_started",
        "step_completed",
        "run_failed",
    ]
    assert events[-1]["error"] == "simulated stream failure"
    assert events[-1]["node"] == "plan"
    assert events[-1]["snapshot"]["plan"]["topic"] == "AMD research"

    snapshot_response = client.get(f"/api/runs/{run_id}")
    data = snapshot_response.json()
    assert data["status"] == "failed"
    assert data["latest_node"] == "plan"
    assert data["error"] == "simulated stream failure"
    assert data["snapshot"]["plan"]["topic"] == "AMD research"

    next_create_response = client.post("/api/runs", json={"query": "Research MSFT"})
    assert next_create_response.status_code == 201


def test_sse_stream_replays_backlog_without_starting_second_execution() -> None:
    store = InMemoryRunStore()
    query = "Research META"
    agent_created = 0

    def agent_factory() -> FakeAgent:
        nonlocal agent_created
        agent_created += 1
        return FakeAgent(_build_success_events(query))

    client = TestClient(create_app(run_store=store, agent_factory=agent_factory))

    create_response = client.post("/api/runs", json={"query": query})
    run_id = create_response.json()["run_id"]

    with client.stream("GET", f"/api/runs/{run_id}/events") as first_response:
        first_events = _collect_sse_events(first_response)

    with client.stream("GET", f"/api/runs/{run_id}/events") as replay_response:
        replay_events = _collect_sse_events(replay_response)

    assert [event["type"] for event in first_events] == [event["type"] for event in replay_events]
    assert [event["timestamp"] for event in first_events] == [event["timestamp"] for event in replay_events]
    assert agent_created == 1


def test_sse_stream_returns_404_for_unknown_run() -> None:
    client = TestClient(create_app(run_store=InMemoryRunStore()))

    response = client.get("/api/runs/not-a-real-id/events")

    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found."}
