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

    def _get_active_run_unlocked(self) -> RunRecord | None:
        if self._active_run_id is None:
            return None
        return self._runs.get(self._active_run_id)


__all__ = [
    "ActiveRunConflictError",
    "InMemoryRunStore",
    "RunRecord",
    "RunStatus",
]
