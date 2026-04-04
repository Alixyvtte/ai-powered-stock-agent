from __future__ import annotations

from fastapi.testclient import TestClient

from stock_agent.web.app import create_app


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok"}


def test_index_renders_workbench_shell() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "Stock Agent Analyst Workbench" in html
    for region_id in [
        "app-header",
        "query-form",
        "run-status",
        "timeline-panel",
        "detail-panel",
        "report-panel",
        "summary-panel",
    ]:
        assert f'id="{region_id}"' in html

    for step in ["plan", "market", "search_web", "extract", "decide", "write_report"]:
        assert step in html
