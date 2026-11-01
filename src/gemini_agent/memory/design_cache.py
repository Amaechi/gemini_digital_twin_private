"""Design memory: simulated configurations by content hash with their KPIs (spec §10.2).

A simulation or economics result depends only on the configuration it was computed from, so the
cache key is a SHA-256 of the configuration's **canonical JSON** plus a namespace naming the tool,
its mode and version (e.g. ``"simulate_annual:full:v1"``; bump the version when the model
changes). Canonical JSON: keys sorted, ``_``-prefixed keys dropped (comments, the GEMINI project
convention), integers written as floats (``2`` and ``2.0`` hash alike), ``-0.0`` → ``0.0``;
NaN/inf are refused. Identity fields that do not change the physics (``id``, ``description``)
can be excluded with ``ignore``.

Only deterministic tool outputs belong here (never stub results). On a hit for a different
candidate the stored KPIs are returned relabelled with the requesting candidate's id.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, JsonValue

from gemini_agent.schemas.common import StrictModel
from gemini_agent.schemas.results import KPISet

CACHE_FILE = "design_cache.sqlite"
"""Default file name (shared by the runs of one project, e.g. ``runs/design_cache.sqlite``)."""
CACHE_SUFFIXES = (".sqlite", ".sqlite3", ".db")
"""A path with one of these suffixes is the cache file; any other path is a directory."""


def _normalise(value: Any, ignore: frozenset[str]) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(k): _normalise(v, ignore)
            for k, v in value.items()
            if not str(k).startswith("_") and str(k) not in ignore
        }
    if isinstance(value, list | tuple):
        return [_normalise(v, ignore) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"configuration contains a non-finite number: {value!r}")
        return number + 0.0  # -0.0 → 0.0
    if hasattr(value, "tolist"):  # numpy scalars/arrays
        return _normalise(value.tolist(), ignore)
    raise TypeError(f"cannot hash value of type {type(value).__name__}")


def canonical_json(config: Any, *, ignore: Iterable[str] = ()) -> str:
    """Canonical JSON text of a configuration (see module docstring)."""
    normalised = _normalise(config, frozenset(ignore))
    return json.dumps(normalised, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def config_hash(config: Any, *, namespace: str = "", ignore: Iterable[str] = ()) -> str:
    """SHA-256 hex digest of ``namespace`` + canonical JSON of ``config``."""
    text = namespace + "\n" + canonical_json(config, ignore=ignore)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CacheEntry(StrictModel):
    """One cached result."""

    config_hash: str
    namespace: str
    candidate_id: str | None = None
    kpis: KPISet | None = None
    result: dict[str, JsonValue] = Field(default_factory=dict)
    summary: str = ""
    created_at: str
    source_run: str | None = None
    hits: int = Field(default=0, ge=0)


class DesignCache:
    """SQLite-backed design cache (thread-safe, one file per project)."""

    def __init__(self, path: str | Path) -> None:
        """Open (or create) the cache; a directory path gets ``design_cache.sqlite`` inside."""
        target = Path(path)
        is_file = target.suffix in CACHE_SUFFIXES and not target.is_dir()
        self.path = target if is_file else target / CACHE_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS designs (config_hash TEXT PRIMARY KEY, "
                "namespace TEXT NOT NULL, config TEXT NOT NULL, entry TEXT NOT NULL, "
                "hits INTEGER NOT NULL DEFAULT 0)"
            )

    @staticmethod
    def key(config: Any, namespace: str, ignore: Iterable[str] = ()) -> str:
        """Cache key of a configuration in a namespace."""
        return config_hash(config, namespace=namespace, ignore=ignore)

    def get(self, key: str, candidate_id: str | None = None) -> CacheEntry | None:
        """Return the entry (counting a hit), KPIs relabelled for ``candidate_id`` if given."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT entry, hits FROM designs WHERE config_hash = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            self._conn.execute("UPDATE designs SET hits = hits + 1 WHERE config_hash = ?", (key,))
        entry = CacheEntry.model_validate_json(row["entry"]).model_copy(
            update={"hits": int(row["hits"]) + 1}
        )
        if candidate_id is not None and entry.kpis is not None:
            entry.kpis = entry.kpis.model_copy(update={"candidate_id": candidate_id})
            entry.candidate_id = candidate_id
        return entry

    def put(
        self,
        config: Any,
        *,
        namespace: str,
        kpis: KPISet | None = None,
        result: dict[str, Any] | None = None,
        summary: str = "",
        candidate_id: str | None = None,
        run_id: str | None = None,
        ignore: Iterable[str] = (),
        stub: bool = False,
    ) -> CacheEntry:
        """Store (or overwrite) the result for ``config`` and return the entry.

        ``stub=True`` raises: a stub result must never be served to a later run as real.
        """
        if stub:
            raise ValueError("stub tool results are never cached")
        ignore = tuple(ignore)
        key = self.key(config, namespace, ignore)
        entry = CacheEntry(
            config_hash=key,
            namespace=namespace,
            candidate_id=candidate_id or (kpis.candidate_id if kpis else None),
            kpis=kpis,
            result=result or {},
            summary=summary,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            source_run=run_id,
        )
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO designs (config_hash, namespace, config, entry, hits) "
                "VALUES (?, ?, ?, ?, 0)",
                (key, namespace, canonical_json(config, ignore=ignore), entry.model_dump_json()),
            )
        return entry

    def get_or_compute(
        self,
        config: Any,
        compute: Callable[[], tuple[KPISet | None, dict[str, Any]]],
        *,
        namespace: str,
        candidate_id: str | None = None,
        run_id: str | None = None,
        ignore: Iterable[str] = (),
    ) -> tuple[CacheEntry, bool]:
        """Return ``(entry, hit)``; on a miss run ``compute() -> (kpis, result)`` and store it."""
        ignore = tuple(ignore)
        cached = self.get(self.key(config, namespace, ignore), candidate_id)
        if cached is not None:
            return cached, True
        kpis, result = compute()
        entry = self.put(
            config,
            namespace=namespace,
            kpis=kpis,
            result=result,
            candidate_id=candidate_id,
            run_id=run_id,
            ignore=ignore,
        )
        return entry, False

    def entries(self, namespace: str | None = None) -> list[CacheEntry]:
        """Return all entries, optionally of one namespace."""
        query, params = "SELECT entry, hits FROM designs", ()
        if namespace is not None:
            query, params = query + " WHERE namespace = ?", (namespace,)
        with self._lock:
            rows = self._conn.execute(query + " ORDER BY rowid", params).fetchall()
        return [
            CacheEntry.model_validate_json(row["entry"]).model_copy(update={"hits": row["hits"]})
            for row in rows
        ]

    def stats(self) -> dict[str, int]:
        """Count entries and total hits (compute-saving evidence, MEM-06)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(hits), 0) AS hits FROM designs"
            ).fetchone()
        return {"entries": int(row["n"]), "hits": int(row["hits"])}

    def close(self) -> None:
        """Close the connection."""
        with self._lock:
            self._conn.close()
