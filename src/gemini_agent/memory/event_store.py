# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# files services/plant-debugger/plant_debugger/persistence/base.py and sqlite_store.py @ d69507a.
# Modified by GeoAgent team, 2026-11-01: incidents replaced by workflow runs; synchronous API;
# run state stored as JSON for pause/resume (WP-10). WP-09: append-only triggers, event filters,
# run listing, JSONL export and replay of a run's trace.
"""Episodic memory: append-only event log and persisted run state (spec §10.2, AG-10).

The workflow runner (WP-10) uses ``create_run`` / ``get_run`` / ``update_run`` /
``append_event`` / ``list_events`` / ``close`` for pause/resume; those signatures are stable.
Events are append-only: SQLite triggers refuse UPDATE and DELETE on the ``events`` table.
``replay`` re-reads a run's events in order and rebuilds its trace (phases, decisions, tool
calls, verifier gates, durable user answers, final status) — AG-10 "trace replayable".
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue

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


ReplayStatus = Literal["running", "paused", "completed", "escalated", "refused"]
"""Run status as reconstructed from the events (same values as ``RunStatus``)."""

TERMINAL_EVENTS: dict[str, ReplayStatus] = {
    "RUN_END": "completed",
    "ESCALATE": "escalated",
    "REFUSE": "refused",
    "PAUSE": "paused",
}
"""Event types that set the run status; a later ``RESUME`` sets it back to running."""


class RunReplay(StrictModel):
    """Trace of one run rebuilt from its events (no LLM, no tool re-execution)."""

    run_id: str
    events: int
    first_ts: str | None = None
    last_ts: str | None = None
    status: ReplayStatus = "running"
    phases: list[str] = Field(default_factory=list, description="PHASE_START order (loops).")
    counts: dict[str, int] = Field(default_factory=dict, description="Events per type.")
    decision_sources: dict[str, int] = Field(default_factory=dict)
    tool_calls: list[dict[str, JsonValue]] = Field(default_factory=list)
    tool_errors: int = 0
    stub_tool_calls: int = 0
    decision_records: list[str] = Field(default_factory=list)
    verdicts: list[dict[str, JsonValue]] = Field(default_factory=list)
    user_answers: list[dict[str, JsonValue]] = Field(
        default_factory=list, description="Durable USER_ANSWER events (MEM-01)."
    )
    loop_backs: int = 0
    final_message: str | None = None
    missing_seq: list[int] = Field(
        default_factory=list, description="Sequence numbers absent from 1..n (should be empty)."
    )

    @property
    def last_verdict(self) -> str | None:
        """Verdict of the most recent verifier gate, if any."""
        return str(self.verdicts[-1].get("verdict")) if self.verdicts else None


def replay_events(
    run_id: str,
    events: Iterable[EventRecord],
    on_event: Callable[[EventRecord], None] | None = None,
) -> RunReplay:
    """Fold events (in order) into a ``RunReplay``; ``on_event`` sees each event first."""
    trace = RunReplay(run_id=run_id, events=0)
    counts: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    seqs: list[int] = []
    for event in events:
        if on_event is not None:
            on_event(event)
        trace.events += 1
        seqs.append(event.seq)
        trace.first_ts = trace.first_ts or event.ts
        trace.last_ts = event.ts
        counts[event.type] += 1
        payload = event.payload
        if event.type == "PHASE_START" and event.phase:
            trace.phases.append(event.phase)
        elif event.type == "DECISION":
            sources[str(payload.get("source"))] += 1
        elif event.type == "TOOL_RESULT":
            trace.tool_calls.append(
                {
                    "seq": event.seq,
                    "phase": event.phase,
                    **{k: payload.get(k) for k in ("id", "tool", "stub", "source")},
                }
            )
            trace.stub_tool_calls += int(bool(payload.get("stub")))
        elif event.type == "TOOL_ERROR":
            trace.tool_errors += 1
        elif event.type == "DECISION_RECORD":
            trace.decision_records.append(str(payload.get("dr_id")))
        elif event.type == "VERIFY_GATE":
            trace.verdicts.append(
                {"phase": event.phase, **{k: payload.get(k) for k in ("candidate_id", "verdict")}}
            )
        elif event.type == "USER_ANSWER" and payload.get("durable") is True:
            trace.user_answers.append({"seq": event.seq, **payload})
        elif event.type == "LOOP_BACK":
            trace.loop_backs += 1
        if event.type in TERMINAL_EVENTS:
            trace.status = TERMINAL_EVENTS[event.type]
            if event.type != "PAUSE":
                trace.final_message = str(payload.get("message", "")) or None
        elif event.type == "RESUME":
            trace.status = "running"
    trace.counts = dict(counts)
    trace.decision_sources = dict(sources)
    if seqs:
        present = set(seqs)
        trace.missing_seq = [n for n in range(1, max(seqs) + 1) if n not in present]
    return trace


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
    def list_events(
        self,
        run_id: str,
        *,
        types: Iterable[str] | None = None,
        phase: str | None = None,
        after_seq: int = 0,
    ) -> list[EventRecord]:
        """Return the events of a run in order, optionally filtered by type, phase or seq."""

    @abstractmethod
    def list_runs(self, status: str | None = None) -> list[RunRecord]:
        """Return all runs (optionally with one status), oldest first."""

    @abstractmethod
    def last_event(self, run_id: str) -> EventRecord | None:
        """Return the most recent event of a run, or None."""

    @abstractmethod
    def close(self) -> None:
        """Release the storage."""

    def replay(
        self, run_id: str, on_event: Callable[[EventRecord], None] | None = None
    ) -> RunReplay:
        """Rebuild the run's trace from its events (KeyError if the run is unknown)."""
        events = self.list_events(run_id)
        if not events and self.get_run(run_id) is None:
            raise KeyError(run_id)
        return replay_events(run_id, events, on_event)

    def export_jsonl(self, run_id: str, path: str | Path) -> Path:
        """Write the run's events as JSON lines (``runs/<id>/events.jsonl``) and return the path."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = (event.model_dump_json() + "\n" for event in self.list_events(run_id))
        target.write_text("".join(lines), encoding="utf-8")
        return target


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


_APPEND_ONLY_TRIGGERS = tuple(
    f"CREATE TRIGGER IF NOT EXISTS events_no_{op.lower()} BEFORE {op} ON events "
    "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END"
    for op in ("UPDATE", "DELETE")
)


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
            for trigger in _APPEND_ONLY_TRIGGERS:
                self._conn.execute(trigger)

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

    @staticmethod
    def _run(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            state=json.loads(row["state"]),
        )

    def get_run(self, run_id: str) -> RunRecord | None:
        """Return a run, or None if unknown."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else self._run(row)

    def list_runs(self, status: str | None = None) -> list[RunRecord]:
        """All runs in this store (optionally with one status), oldest first."""
        query, params = "SELECT * FROM runs", ()
        if status is not None:
            query, params = query + " WHERE status = ?", (status,)
        with self._lock:
            rows = self._conn.execute(query + " ORDER BY created_at, run_id", params).fetchall()
        return [self._run(row) for row in rows]

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

    def list_events(
        self,
        run_id: str,
        *,
        types: Iterable[str] | None = None,
        phase: str | None = None,
        after_seq: int = 0,
    ) -> list[EventRecord]:
        """Return the events of a run in order, optionally filtered by type, phase or seq."""
        query = "SELECT * FROM events WHERE run_id = ? AND seq > ?"
        params: list[Any] = [run_id, after_seq]
        wanted = sorted(set(types)) if types is not None else None
        if wanted is not None:
            query += f" AND type IN ({', '.join('?' for _ in wanted)})"
            params += wanted
        if phase is not None:
            query += " AND phase = ?"
            params.append(phase)
        with self._lock:
            rows = self._conn.execute(query + " ORDER BY seq", params).fetchall()
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

    def last_event(self, run_id: str) -> EventRecord | None:
        """Return the most recent event of a run, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(seq) AS last FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()
        last = row["last"]
        return None if last is None else self.list_events(run_id, after_seq=int(last) - 1)[0]

    def close(self) -> None:
        """Close the connection."""
        with self._lock:
            self._conn.close()
