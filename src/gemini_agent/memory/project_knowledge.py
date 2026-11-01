# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# files services/plant-debugger/plant_debugger/field_ops/site_knowledge.py and
# field_ops/models.py @ d69507a. Modified by GeoAgent team, 2026-11-01: JSON store only
# (Firestore dropped), synchronous API, SiteKnowledge → ProjectFact keyed by assumption id,
# ingestion of durable USER_ANSWER events from the workflow event log.
"""Project knowledge: explicit user-confirmed facts only (spec §10.2, MEM-01).

A fact is either a user's answer to a design-basis question (a ``USER_ANSWER`` event with
``durable: true`` and an ``assumption_id``) or an assertion the user typed explicitly
(``new_user_assertion``). Register defaults the runner assumed (``ASSUMPTION`` events), LLM
output, tool results and the design approval are **never** stored: the ``source`` field only
admits the two user sources, and ``ingest_events`` ignores every other event.

One fact per assumption id (the latest answer wins); free assertions are de-duplicated by
case-insensitive text, as in Plant Debugger's ``JsonSiteKnowledgeStore.save``.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue

from gemini_agent.memory.event_store import EventRecord
from gemini_agent.schemas.common import StrictModel

KNOWLEDGE_FILE = "project_knowledge.json"
"""Default file name (shared by the runs of one project, e.g. ``runs/project_knowledge.json``)."""

UserSource = Literal["user_answer", "user_assertion"]
"""The only sources project knowledge accepts (MEM-01)."""


class ProjectFact(StrictModel):
    """One durable, user-confirmed fact (Plant Debugger ``SiteKnowledge``)."""

    knowledge_id: str
    assertion: str = Field(min_length=3, max_length=500)
    assumption_id: str | None = Field(default=None, description="Register id, e.g. 'DB-31'.")
    value: JsonValue = None
    source: UserSource
    learned_at: str
    learned_in_run: str | None = None
    event_seq: int | None = Field(default=None, ge=1)
    use_count: int = Field(default=0, ge=0)
    last_used_at: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_user_assertion(
    *,
    assertion: str,
    run_id: str | None = None,
    assumption_id: str | None = None,
    value: JsonValue = None,
    source: UserSource = "user_assertion",
    event_seq: int | None = None,
) -> ProjectFact:
    """Build a fact from text the user stated (whitespace collapsed, 3–500 characters)."""
    text = " ".join(assertion.strip().split())
    if len(text) < 3:
        raise ValueError("project knowledge assertion is too short")
    if len(text) > 500:
        raise ValueError("project knowledge assertion exceeds 500 characters")
    return ProjectFact(
        knowledge_id=f"knowledge-{uuid.uuid4().hex[:12]}",
        assertion=text,
        assumption_id=assumption_id,
        value=value,
        source=source,
        learned_at=_now(),
        learned_in_run=run_id,
        event_seq=event_seq,
    )


def fact_from_event(event: EventRecord) -> ProjectFact | None:
    """Fact for a durable user answer to a register question; None for any other event."""
    payload = event.payload
    assumption_id = payload.get("assumption_id")
    if (
        event.type != "USER_ANSWER"
        or payload.get("durable") is not True
        or not isinstance(assumption_id, str)
        or not assumption_id
    ):
        return None
    message = payload.get("message")
    text = message if isinstance(message, str) and message else f"{assumption_id} answered"
    return new_user_assertion(
        assertion=text,
        run_id=event.run_id,
        assumption_id=assumption_id,
        value=payload.get("answer"),
        source="user_answer",
        event_seq=event.seq,
    )


class JsonProjectKnowledgeStore:
    """JSON-file store (atomic replace on write), Plant Debugger ``JsonSiteKnowledgeStore``."""

    def __init__(self, path: str | Path) -> None:
        """Use ``path``: a ``.json`` file, or a directory that gets ``project_knowledge.json``."""
        target = Path(path)
        is_file = target.suffix == ".json" and not target.is_dir()
        self.path = target if is_file else target / KNOWLEDGE_FILE
        self._lock = threading.RLock()

    def list(self) -> list[ProjectFact]:
        """All facts, oldest first."""
        with self._lock:
            if not self.path.exists():
                return []
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        return [ProjectFact.model_validate(item) for item in payload]

    def save(self, item: ProjectFact) -> ProjectFact:
        """Store a fact; it replaces the fact with the same assumption id or the same text."""
        fact = ProjectFact.model_validate(item.model_dump())  # re-check even model_construct
        with self._lock:
            items = [existing for existing in self.list() if not _same(existing, fact)]
            items.append(fact)
            self.replace(items)
        return fact

    def replace(self, items: Iterable[ProjectFact]) -> None:
        """Rewrite the file with ``items`` (validated) atomically."""
        facts = [ProjectFact.model_validate(item.model_dump()) for item in items]
        payload = json.dumps([fact.model_dump(mode="json") for fact in facts], indent=2)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.path)

    def clear(self) -> None:
        """Delete every fact."""
        with self._lock:
            self.path.unlink(missing_ok=True)

    def get(self, assumption_id: str) -> ProjectFact | None:
        """Return the fact recorded for a register id, or None."""
        return next((f for f in self.list() if f.assumption_id == assumption_id), None)

    def answers(self) -> dict[str, JsonValue]:
        """Register id → user-confirmed value (for pre-filling a later run's P1 gaps)."""
        return {f.assumption_id: f.value for f in self.list() if f.assumption_id}

    def mark_used(self, knowledge_id: str) -> ProjectFact:
        """Count one use of a fact (KeyError if unknown)."""
        with self._lock:
            items = self.list()
            for index, fact in enumerate(items):
                if fact.knowledge_id == knowledge_id:
                    items[index] = fact.model_copy(
                        update={"use_count": fact.use_count + 1, "last_used_at": _now()}
                    )
                    self.replace(items)
                    return items[index]
        raise KeyError(knowledge_id)

    def ingest_events(self, events: Iterable[EventRecord]) -> list[ProjectFact]:
        """Store the durable user answers among ``events``; return the facts stored."""
        stored = []
        for event in events:
            fact = fact_from_event(event)
            if fact is not None:
                stored.append(self.save(fact))
        return stored


def _same(a: ProjectFact, b: ProjectFact) -> bool:
    if a.assumption_id or b.assumption_id:
        return a.assumption_id == b.assumption_id
    return a.assertion.strip().casefold() == b.assertion.strip().casefold()
