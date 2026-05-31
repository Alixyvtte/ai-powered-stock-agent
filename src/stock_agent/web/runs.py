from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Condition, Lock
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
    mode: str = "standard"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latest_node: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)
    summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    final_report: str | None = None
    error: str | None = None
    execution_started: bool = False


class ActiveRunConflictError(RuntimeError):
    """Raised when a new run is created while another active run exists."""


class InMemoryRunStore:
    def __init__(self, persistence: Any = None) -> None:
        self._lock = Lock()
        self._runs: dict[str, RunRecord] = {}
        self._active_run_id: str | None = None
        self._conditions: dict[str, Condition] = {}
        # Optional write-through persistence (e.g. SqliteRunPersistence). Live
        # event streaming stays fully in-memory; only finished runs are persisted.
        self._persistence = persistence
        if persistence is not None:
            try:
                for record in persistence.load_all():
                    self._runs[record.run_id] = record
                    self._conditions[record.run_id] = Condition(self._lock)
            except Exception:
                pass

    def _persist_unlocked(self, run: RunRecord) -> None:
        if self._persistence is not None:
            self._persistence.save_run(run)

    def create_run(self, query: str, mode: str = "standard") -> RunRecord:
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
                mode=mode,
            )
            self._runs[run.run_id] = run
            self._active_run_id = run.run_id
            self._conditions[run.run_id] = Condition(self._lock)
            self._persist_unlocked(run)
            return run

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def get_active_run(self) -> RunRecord | None:
        with self._lock:
            return self._get_active_run_unlocked()

    def list_runs(self) -> list[RunRecord]:
        """All runs, newest first (for the run-history view)."""
        with self._lock:
            return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    def cancel_run(self, run_id: str) -> RunRecord:
        """Soft-cancel: mark the run failed, free the active slot, and notify the
        SSE stream to end. An already-terminal run is returned unchanged."""
        with self._lock:
            run = self._require_run_unlocked(run_id)
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                return run
            now = datetime.now(timezone.utc)
            run.status = RunStatus.FAILED
            run.error = "Run cancelled by user."
            run.finished_at = now
            run.updated_at = now
            if self._active_run_id == run_id:
                self._active_run_id = None
            self._persist_unlocked(run)
            self._conditions[run_id].notify_all()
            return run

    def start_run(self, run_id: str) -> tuple[RunRecord, bool]:
        with self._lock:
            run = self._require_run_unlocked(run_id)
            if run.status is RunStatus.QUEUED and not run.execution_started:
                now = datetime.now(timezone.utc)
                run.execution_started = True
                run.status = RunStatus.RUNNING
                run.started_at = run.started_at or now
                run.updated_at = now
                self._active_run_id = run_id
                return run, True
            return run, False

    def append_event(self, run_id: str, event: dict[str, Any]) -> RunRecord:
        with self._lock:
            run = self._require_run_unlocked(run_id)
            # Ignore late events for an already-terminal run (e.g. an orphaned
            # worker after a cancel) so it cannot be silently "un-cancelled".
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                return run
            stored_event = deepcopy(event)
            run.events.append(stored_event)

            now = datetime.now(timezone.utc)
            event_type = str(stored_event.get("type") or "")
            snapshot = stored_event.get("snapshot")
            if isinstance(snapshot, dict):
                run.snapshot = deepcopy(snapshot)

            if event_type == "run_started":
                run.status = RunStatus.RUNNING
                run.started_at = run.started_at or now
                run.error = None
                run.updated_at = now
            elif event_type == "step_completed":
                run.status = RunStatus.RUNNING
                run.started_at = run.started_at or now
                run.latest_node = str(stored_event.get("node") or "") or None
                summary = stored_event.get("summary")
                if run.latest_node and isinstance(summary, dict):
                    run.summaries = self._merge_summaries(run.summaries, {run.latest_node: deepcopy(summary)})
                run.updated_at = now
            elif event_type == "run_completed":
                run.status = RunStatus.COMPLETED
                run.final_report = str(stored_event.get("final_report") or "")
                run.error = None
                run.finished_at = now
                run.updated_at = now
                if self._active_run_id == run_id:
                    self._active_run_id = None
            elif event_type == "run_failed":
                run.status = RunStatus.FAILED
                node = str(stored_event.get("node") or "") or None
                if node is not None:
                    run.latest_node = node
                run.error = str(stored_event.get("error") or "")
                run.finished_at = now
                run.updated_at = now
                if self._active_run_id == run_id:
                    self._active_run_id = None
            else:
                run.updated_at = now

            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                self._persist_unlocked(run)

            self._conditions[run_id].notify_all()
            return run

    def wait_for_events(
        self,
        run_id: str,
        after_index: int,
        *,
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        condition = self._conditions.get(run_id)
        if condition is None:
            raise KeyError(run_id)

        with condition:
            run = self._require_run_unlocked(run_id)
            condition.wait_for(
                lambda: len(run.events) > after_index or run.status in {RunStatus.COMPLETED, RunStatus.FAILED},
                timeout=timeout,
            )
            run = self._require_run_unlocked(run_id)
            return deepcopy(run.events[after_index:])

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
            self._conditions[run_id].notify_all()
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
            self._persist_unlocked(run)
            self._conditions[run_id].notify_all()
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
            self._persist_unlocked(run)
            self._conditions[run_id].notify_all()
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
