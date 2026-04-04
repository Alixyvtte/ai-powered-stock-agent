from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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
    assert data["error"] is None


def test_get_run_snapshot_returns_completed_state() -> None:
    store = InMemoryRunStore()
    client = TestClient(create_app(run_store=store))

    create_response = client.post("/api/runs", json={"query": "Analyze Microsoft cloud growth"})
    run_id = create_response.json()["run_id"]
    store.record_progress(run_id, latest_node="write_report", snapshot={"notes": [{"id": 1}]})
    store.complete_run(
        run_id,
        final_report="Completed report",
        snapshot={"final_report": "Completed report", "notes": [{"id": 1}]},
        summaries={"write_report": {"report_length": 16}},
    )

    response = client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["finished_at"]
    assert data["latest_node"] == "write_report"
    assert data["snapshot"] == {"final_report": "Completed report", "notes": [{"id": 1}]}
    assert data["summaries"] == {"write_report": {"report_length": 16}}
    assert data["final_report"] == "Completed report"
    assert data["error"] is None


def test_get_run_snapshot_returns_404_for_unknown_run() -> None:
    client = TestClient(create_app(run_store=InMemoryRunStore()))

    response = client.get("/api/runs/not-a-real-id")

    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found."}
