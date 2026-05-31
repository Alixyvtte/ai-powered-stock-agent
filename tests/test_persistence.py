from __future__ import annotations

from fastapi.testclient import TestClient

from stock_agent.web.app import create_app
from stock_agent.web.persistence import SqliteRunPersistence
from stock_agent.web.runs import InMemoryRunStore, RunStatus


def test_persistence_roundtrip_and_recovery(tmp_path):
    db = str(tmp_path / "runs.db")
    store = InMemoryRunStore(persistence=SqliteRunPersistence(db))
    run = store.create_run("Analyze NVDA", mode="deep")
    store.complete_run(
        run.run_id,
        final_report="Memo [S1]",
        snapshot={"query": "Analyze NVDA", "thesis": {"verdict": "bullish"}},
    )

    # A fresh store backed by the same DB recovers the finished run.
    recovered = InMemoryRunStore(persistence=SqliteRunPersistence(db))
    got = recovered.get_run(run.run_id)
    assert got is not None
    assert got.status is RunStatus.COMPLETED
    assert got.mode == "deep"
    assert got.final_report == "Memo [S1]"
    assert got.snapshot["thesis"]["verdict"] == "bullish"


def test_list_runs_newest_first():
    store = InMemoryRunStore()
    a = store.create_run("first")
    store.complete_run(a.run_id, final_report="r", snapshot={})
    store.create_run("second")
    listed = store.list_runs()
    assert [r.query for r in listed][:2] == ["second", "first"]


def test_cancel_run_frees_active_slot():
    store = InMemoryRunStore()
    run = store.create_run("running query")
    store.start_run(run.run_id)

    cancelled = store.cancel_run(run.run_id)
    assert cancelled.status is RunStatus.FAILED
    assert "cancel" in (cancelled.error or "").lower()

    # active slot freed -> a new run can be created
    nxt = store.create_run("next query")
    assert nxt.run_id != run.run_id


def test_cancelled_run_ignores_late_events():
    store = InMemoryRunStore()
    run = store.create_run("q")
    store.start_run(run.run_id)
    store.cancel_run(run.run_id)

    # an orphaned worker completing later must not un-cancel it
    from stock_agent.event_adapter import build_run_completed_event

    store.append_event(run.run_id, build_run_completed_event({"final_report": "late"}))
    assert store.get_run(run.run_id).status is RunStatus.FAILED


def test_runs_history_and_cancel_endpoints():
    store = InMemoryRunStore()
    client = TestClient(create_app(run_store=store))

    created = client.post("/api/runs", json={"query": "Analyze NVDA"}).json()
    run_id = created["run_id"]

    listing = client.get("/api/runs")
    assert listing.status_code == 200
    assert any(item["run_id"] == run_id for item in listing.json())

    cancel = client.post(f"/api/runs/{run_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "failed"

    assert client.post("/api/runs/unknown/cancel").status_code == 404
