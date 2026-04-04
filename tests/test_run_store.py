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
