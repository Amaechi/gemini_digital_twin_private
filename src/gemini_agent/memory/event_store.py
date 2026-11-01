# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# files services/plant-debugger/plant_debugger/persistence/base.py and sqlite_store.py @ d69507a.
# Modified by GeoAgent team, 2026-11-01: incidents replaced by workflow runs; synchronous API;
# run state stored as JSON for pause/resume.
"""Episodic memory: append-only event log and persisted run state (spec §10.2, AG-10).

minimal stub — WP-09 owns and extends (replay, design cache, project knowledge). WP-10 needs only
``create_run`` / ``get_run`` / ``update_run`` / ``append_event`` / ``list_events`` for the
workflow runner's pause/resume; keep these signatures when extending.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from gemini_agent.schemas.common import StrictModel


class EventRecord(StrictModel):
    """One append-only event of a run."""

    run_id: str
    seq: int
    ts: str
    type: str
    phase: str | None = None
    payload: dict[str, JsonValue]


class RunRecord(StrictModel):
    """Persisted run header plus the runner's serialised state."""

    run_id: str
    status: str
    created_at: str
    updated_at: str
    state: dict[str, Any]


class EventStore(ABC):
    """Storage interface used by the workflow runner."""

    @abstractmethod
    def create_run(self, run_id: str, status: str, state: dict[str, Any]) -> RunRecord:
        """Create a run with its initial state."""

    @abstractmethod
    def get_run(self, run_id: str) -> RunRecord | None:
        """Return a run, or None if unknown."""

    @abstractmethod
    def update_run(self, run_id: str, status: str, state: dict[str, Any]) -> RunRecord:
        """Replace the status and state of a run (KeyError if unknown)."""

    @abstractmethod
    def append_event(
        self, run_id: str, type: str, payload: dict[str, Any], phase: str | None = None
    ) -> EventRecord:
        """Append one event and return it with its sequence number."""

    @abstractmethod
    def list_events(self, run_id: str) -> list[EventRecord]:
        """Return the events of a run in order."""

    @abstractmethod
    def close(self) -> None:
        """Release the storage."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class SQLiteEventStore(EventStore):
    """SQLite implementation (one file per run directory or shared)."""

    def __init__(self, path: str | Path) -> None:
        """Open (or create) the SQLite file and its tables."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS events (run_id TEXT NOT NULL, seq INTEGER NOT NULL, "
                "ts TEXT NOT NULL, type TEXT NOT NULL, phase TEXT, payload TEXT NOT NULL, "
                "PRIMARY KEY (run_id, seq))"
            )

    def create_run(self, run_id: str, status: str, state: dict[str, Any]) -> RunRecord:
        """Create a run with its initial state."""
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO runs (run_id, status, created_at, updated_at, state) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, status, now, now, json.dumps(state)),
            )
        return RunRecord(run_id=run_id, status=status, created_at=now, updated_at=now, state=state)

    def get_run(self, run_id: str) -> RunRecord | None:
        """Return a run, or None if unknown."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return RunRecord(
            run_id=row["run_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            state=json.loads(row["state"]),
        )

    def update_run(self, run_id: str, status: str, state: dict[str, Any]) -> RunRecord:
        """Replace the status and state of a run (KeyError if unknown)."""
        now = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE runs SET status = ?, updated_at = ?, state = ? WHERE run_id = ?",
                (status, now, json.dumps(state), run_id),
            )
            if cur.rowcount != 1:
                raise KeyError(run_id)
        record = self.get_run(run_id)
        assert record is not None
        return record

    def append_event(
        self, run_id: str, type: str, payload: dict[str, Any], phase: str | None = None
    ) -> EventRecord:
        """Append one event and return it with its sequence number."""
        now = _now()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS last FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()
            seq = int(row["last"]) + 1
            self._conn.execute(
                "INSERT INTO events (run_id, seq, ts, type, phase, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, seq, now, type, phase, json.dumps(payload)),
            )
        return EventRecord(run_id=run_id, seq=seq, ts=now, type=type, phase=phase, payload=payload)

    def list_events(self, run_id: str) -> list[EventRecord]:
        """Return the events of a run in order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        return [
            EventRecord(
                run_id=row["run_id"],
                seq=row["seq"],
                ts=row["ts"],
                type=row["type"],
                phase=row["phase"],
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    def close(self) -> None:
        """Close the connection."""
        with self._lock:
            self._conn.close()
