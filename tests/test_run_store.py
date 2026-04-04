from __future__ import annotations

from datetime import timezone

import pytest

from stock_agent.web.runs import ActiveRunConflictError, InMemoryRunStore, RunStatus


def test_create_run_stores_record_and_marks_it_active() -> None:
    store = InMemoryRunStore()

    record = store.create_run("  Research NVDA valuation  ")

    assert record.query == "Research NVDA valuation"
    assert record.status is RunStatus.QUEUED
    assert record.created_at.tzinfo == timezone.utc
    assert record.updated_at == record.created_at
    assert record.snapshot == {}
    assert record.summaries == {}
    assert record.events == []
    assert store.get_run(record.run_id) is record
    assert store.get_active_run() is record


def test_only_queued_or_running_runs_block_new_create() -> None:
    store = InMemoryRunStore()
    first = store.create_run("First query")

    with pytest.raises(ActiveRunConflictError):
        store.create_run("Second query")

    first.status = RunStatus.RUNNING
    with pytest.raises(ActiveRunConflictError):
        store.create_run("Second query")

    first.status = RunStatus.COMPLETED
    second = store.create_run("Second query")
    assert second.query == "Second query"
    assert store.get_active_run() is second

    second.status = RunStatus.FAILED
    third = store.create_run("Third query")
    assert third.query == "Third query"
    assert store.get_active_run() is third


def test_get_run_returns_the_stored_record() -> None:
    store = InMemoryRunStore()
    record = store.create_run("Check lookup")

    looked_up = store.get_run(record.run_id)

    assert looked_up is record


def test_record_progress_sets_running_state_and_updates_snapshot() -> None:
    store = InMemoryRunStore()
    record = store.create_run("Track progress")

    store.record_progress(
        record.run_id,
        latest_node="market",
        snapshot={"market": {"NVDA": {"price": 123.45}}},
        summaries={"market": {"ticker_count": 1}},
    )

    assert record.status is RunStatus.RUNNING
    assert record.latest_node == "market"
    assert record.snapshot == {"market": {"NVDA": {"price": 123.45}}}
    assert record.summaries == {"market": {"ticker_count": 1}}
    assert record.started_at is not None
    assert record.updated_at >= record.started_at
    assert store.get_active_run() is record


def test_complete_and_fail_run_release_active_slot_for_next_run() -> None:
    store = InMemoryRunStore()
    first = store.create_run("First query")
    store.record_progress(first.run_id, latest_node="extract", snapshot={"notes": []})

    store.complete_run(
        first.run_id,
        final_report="Final memo",
        snapshot={"final_report": "Final memo", "notes": []},
    )

    assert first.status is RunStatus.COMPLETED
    assert first.final_report == "Final memo"
    assert first.finished_at is not None

    second = store.create_run("Second query")
    store.record_progress(second.run_id, latest_node="search_web", snapshot={"sources": []})
    store.fail_run(
        second.run_id,
        error="Search provider failed",
        snapshot={"sources": []},
        summaries={"search_web": {"source_count": 0}},
    )

    assert second.status is RunStatus.FAILED
    assert second.error == "Search provider failed"
    assert second.finished_at is not None
    assert second.summaries == {"search_web": {"source_count": 0}}

    third = store.create_run("Third query")
    assert third.query == "Third query"
    assert store.get_active_run() is third
