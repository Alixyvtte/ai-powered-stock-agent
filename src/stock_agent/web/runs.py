from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any
from uuid import uuid4


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class RunRecord:
    run_id: str
    query: str
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latest_node: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)
    summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    final_report: str | None = None
    error: str | None = None


class ActiveRunConflictError(RuntimeError):
    """Raised when a new run is created while another active run exists."""


class InMemoryRunStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._runs: dict[str, RunRecord] = {}
        self._active_run_id: str | None = None

    def create_run(self, query: str) -> RunRecord:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Query must not be empty.")

        with self._lock:
            active_run = self._get_active_run_unlocked()
            if active_run and active_run.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
                raise ActiveRunConflictError("Another run is already active.")

            now = datetime.now(timezone.utc)
            run = RunRecord(
                run_id=str(uuid4()),
                query=normalized_query,
                status=RunStatus.QUEUED,
                created_at=now,
                updated_at=now,
            )
            self._runs[run.run_id] = run
            self._active_run_id = run.run_id
            return run

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def get_active_run(self) -> RunRecord | None:
        with self._lock:
            return self._get_active_run_unlocked()

    def record_progress(
        self,
        run_id: str,
        *,
        latest_node: str,
        snapshot: dict[str, Any],
        summaries: dict[str, dict[str, Any]] | None = None,
    ) -> RunRecord:
        with self._lock:
            run = self._require_run_unlocked(run_id)
            now = datetime.now(timezone.utc)
            if run.started_at is None:
                run.started_at = now
            run.status = RunStatus.RUNNING
            run.latest_node = latest_node
            run.snapshot = dict(snapshot)
            if summaries is not None:
                run.summaries = self._merge_summaries(run.summaries, summaries)
            run.updated_at = now
            self._active_run_id = run_id
            return run

    def complete_run(
        self,
        run_id: str,
        *,
        final_report: str,
        snapshot: dict[str, Any],
        latest_node: str = "write_report",
        summaries: dict[str, dict[str, Any]] | None = None,
    ) -> RunRecord:
        with self._lock:
            run = self._require_run_unlocked(run_id)
            now = datetime.now(timezone.utc)
            if run.started_at is None:
                run.started_at = now
            run.status = RunStatus.COMPLETED
            run.latest_node = latest_node
            run.snapshot = dict(snapshot)
            if summaries is not None:
                run.summaries = self._merge_summaries(run.summaries, summaries)
            run.final_report = final_report
            run.error = None
            run.finished_at = now
            run.updated_at = now
            if self._active_run_id == run_id:
                self._active_run_id = None
            return run

    def fail_run(
        self,
        run_id: str,
        *,
        error: str,
        latest_node: str | None = None,
        snapshot: dict[str, Any] | None = None,
        summaries: dict[str, dict[str, Any]] | None = None,
    ) -> RunRecord:
        with self._lock:
            run = self._require_run_unlocked(run_id)
            now = datetime.now(timezone.utc)
            if run.started_at is None:
                run.started_at = now
            run.status = RunStatus.FAILED
            if latest_node is not None:
                run.latest_node = latest_node
            if snapshot is not None:
                run.snapshot = dict(snapshot)
            if summaries is not None:
                run.summaries = self._merge_summaries(run.summaries, summaries)
            run.error = error
            run.finished_at = now
            run.updated_at = now
            if self._active_run_id == run_id:
                self._active_run_id = None
            return run

    def _get_active_run_unlocked(self) -> RunRecord | None:
        if self._active_run_id is None:
            return None
        return self._runs.get(self._active_run_id)

    def _require_run_unlocked(self, run_id: str) -> RunRecord:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    @staticmethod
    def _merge_summaries(
        existing: dict[str, dict[str, Any]],
        incoming: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        merged = {key: dict(value) for key, value in existing.items()}
        for key, value in incoming.items():
            merged[key] = dict(value)
        return merged


__all__ = [
    "ActiveRunConflictError",
    "InMemoryRunStore",
    "RunRecord",
    "RunStatus",
]
