from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..schemas import AlertEvent, IncidentEvent, IncidentRecord, IncidentStatus
from .base import IncidentStore


class SQLiteIncidentStore(IncidentStore):
    def __init__(self, path: str) -> None:
        self.path = path
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY,
                    trigger_event_id TEXT NOT NULL UNIQUE,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incident_events (
                    event_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    step_no INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(incident_id)
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_incident_ts ON incident_events(incident_id, ts)"
            )

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Store is not initialized")
        return self._connection

    async def get_or_create_incident(self, event: AlertEvent) -> tuple[IncidentRecord, bool]:
        return await asyncio.to_thread(self._get_or_create_sync, event)

    def _get_or_create_sync(self, event: AlertEvent) -> tuple[IncidentRecord, bool]:
        with self._lock:
            conn = self._conn()
            row = conn.execute(
                "SELECT payload FROM incidents WHERE trigger_event_id = ?", (event.event_id,)
            ).fetchone()
            if row:
                return IncidentRecord.model_validate_json(row["payload"]), False
            now = datetime.now(UTC)
            incident_id = f"inc-{hashlib.sha256(event.event_id.encode()).hexdigest()[:12]}"
            record = IncidentRecord(
                incident_id=incident_id,
                asset_id=event.asset_id,
                trigger_event_id=event.event_id,
                trigger_payload=event.model_dump(mode="json"),
                status=IncidentStatus.DETECTED,
                started_at=now,
                updated_at=now,
                trace_id=event.trace_id,
            )
            payload = record.model_dump_json()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO incidents VALUES (?, ?, ?, ?, ?)",
                        (incident_id, event.event_id, now.isoformat(), now.isoformat(), payload),
                    )
                return record, True
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT payload FROM incidents WHERE trigger_event_id = ?", (event.event_id,)
                ).fetchone()
                if not row:
                    raise
                return IncidentRecord.model_validate_json(row["payload"]), False

    async def get_incident(self, incident_id: str) -> IncidentRecord | None:
        return await asyncio.to_thread(self._get_incident_sync, incident_id)

    def _get_incident_sync(self, incident_id: str) -> IncidentRecord | None:
        with self._lock:
            row = self._conn().execute(
                "SELECT payload FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            return IncidentRecord.model_validate_json(row["payload"]) if row else None

    async def get_latest_incident(self) -> IncidentRecord | None:
        return await asyncio.to_thread(self._get_latest_sync)

    def _get_latest_sync(self) -> IncidentRecord | None:
        with self._lock:
            row = self._conn().execute(
                "SELECT payload FROM incidents ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return IncidentRecord.model_validate_json(row["payload"]) if row else None

    async def update_incident(self, incident_id: str, **fields: Any) -> IncidentRecord:
        return await asyncio.to_thread(self._update_sync, incident_id, fields)

    def _update_sync(self, incident_id: str, fields: dict[str, Any]) -> IncidentRecord:
        with self._lock:
            conn = self._conn()
            row = conn.execute(
                "SELECT payload FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"Incident not found: {incident_id}")
            record = IncidentRecord.model_validate_json(row["payload"])
            fields["updated_at"] = datetime.now(UTC)
            record = record.model_copy(update=fields)
            payload = record.model_dump_json()
            with conn:
                conn.execute(
                    "UPDATE incidents SET updated_at = ?, payload = ? WHERE incident_id = ?",
                    (record.updated_at.isoformat(), payload, incident_id),
                )
            return record

    async def append_event(self, event: IncidentEvent) -> None:
        await asyncio.to_thread(self._append_event_sync, event)

    def _append_event_sync(self, event: IncidentEvent) -> None:
        with self._lock:
            conn = self._conn()
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO incident_events VALUES (?, ?, ?, ?, ?)",
                    (
                        event.event_id,
                        event.incident_id,
                        event.ts.isoformat(),
                        event.step_no,
                        event.model_dump_json(),
                    ),
                )

    async def list_events(self, incident_id: str) -> list[IncidentEvent]:
        return await asyncio.to_thread(self._list_events_sync, incident_id)

    def _list_events_sync(self, incident_id: str) -> list[IncidentEvent]:
        with self._lock:
            rows = self._conn().execute(
                "SELECT payload FROM incident_events WHERE incident_id = ? ORDER BY ts, rowid",
                (incident_id,),
            ).fetchall()
            return [IncidentEvent.model_validate_json(row["payload"]) for row in rows]

    async def list_incidents(self, limit: int = 20) -> list[IncidentRecord]:
        return await asyncio.to_thread(self._list_incidents_sync, limit)

    def _list_incidents_sync(self, limit: int) -> list[IncidentRecord]:
        with self._lock:
            rows = self._conn().execute(
                "SELECT payload FROM incidents ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [IncidentRecord.model_validate_json(row["payload"]) for row in rows]

    async def clear(self) -> None:
        await asyncio.to_thread(self._clear_sync)

    def _clear_sync(self) -> None:
        with self._lock:
            conn = self._conn()
            with conn:
                conn.execute("DELETE FROM incident_events")
                conn.execute("DELETE FROM incidents")
