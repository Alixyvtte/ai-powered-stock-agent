"""SQLite persistence for run history (survive restarts).

Additive and optional: ``InMemoryRunStore`` keeps its in-memory, Condition-based
live event streaming exactly as before, and merely *writes through* finished runs
here and *loads* them on startup. The raw event stream is not persisted (history
shows the final snapshot + report, which is all the UI needs to re-open a run).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .runs import RunRecord, RunStatus


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


_COLUMNS = (
    "run_id", "query", "status", "mode", "created_at", "updated_at", "started_at",
    "finished_at", "latest_node", "snapshot", "summaries", "final_report", "error",
)


class SqliteRunPersistence:
    def __init__(self, db_path: str) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    query TEXT,
                    status TEXT,
                    mode TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    latest_node TEXT,
                    snapshot TEXT,
                    summaries TEXT,
                    final_report TEXT,
                    error TEXT
                )
                """
            )

    def save_run(self, run: RunRecord) -> None:
        row = (
            run.run_id, run.query, run.status.value, run.mode,
            _iso(run.created_at), _iso(run.updated_at), _iso(run.started_at), _iso(run.finished_at),
            run.latest_node, json.dumps(run.snapshot or {}, ensure_ascii=False),
            json.dumps(run.summaries or {}, ensure_ascii=False), run.final_report, run.error,
        )
        placeholders = ",".join("?" * len(_COLUMNS))
        try:
            with self._lock, self._conn() as conn:
                conn.execute(
                    f"INSERT OR REPLACE INTO runs ({','.join(_COLUMNS)}) VALUES ({placeholders})",
                    row,
                )
        except Exception:
            pass  # persistence must never break the live run

    def load_all(self) -> List[RunRecord]:
        try:
            with self._lock, self._conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"SELECT {','.join(_COLUMNS)} FROM runs ORDER BY created_at"
                ).fetchall()
        except Exception:
            return []

        records: List[RunRecord] = []
        for row in rows:
            try:
                records.append(
                    RunRecord(
                        run_id=row["run_id"],
                        query=row["query"] or "",
                        status=RunStatus(row["status"]),
                        created_at=_parse(row["created_at"]) or datetime.now(),
                        updated_at=_parse(row["updated_at"]) or datetime.now(),
                        mode=row["mode"] or "standard",
                        started_at=_parse(row["started_at"]),
                        finished_at=_parse(row["finished_at"]),
                        latest_node=row["latest_node"],
                        snapshot=json.loads(row["snapshot"] or "{}"),
                        summaries=json.loads(row["summaries"] or "{}"),
                        final_report=row["final_report"],
                        error=row["error"],
                        execution_started=True,
                    )
                )
            except Exception:
                continue
        return records


__all__ = ["SqliteRunPersistence"]
