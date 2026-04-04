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
