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
        "query-input",
        "query-submit",
        "run-status",
        "status-text",
        "status-run-id",
        "status-note",
        "timeline-panel",
        "timeline-list",
        "detail-panel",
        "detail-title",
        "detail-timestamp",
        "detail-summary",
        "detail-warnings",
        "report-panel",
        "report-caution",
        "report-error",
        "report-body",
        "summary-panel",
        "summary-cards",
        "summary-market",
        "summary-market-list",
        "summary-sources",
        "summary-notes",
        "summary-confidence",
        "summary-followups",
        "summary-followups-list",
    ]:
        assert f'id="{region_id}"' in html

    for step in ["plan", "market", "search_web", "extract", "decide", "write_report"]:
        assert step in html
        assert f'data-step="{step}"' in html

    assert 'id="query-input"' in html and "disabled" not in html.split('id="query-input"', 1)[1].split(">", 1)[0]
    assert 'id="query-submit"' in html and "disabled" not in html.split('id="query-submit"', 1)[1].split(">", 1)[0]
