from __future__ import annotations

import asyncio
import inspect
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote

from .models import SiteKnowledge


_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_segment(value: str, fallback: str = "site") -> str:
    cleaned = _SAFE_SEGMENT.sub("-", value.strip()).strip("-._")
    return cleaned or fallback


def _normalize_database(database: str | None) -> str:
    value = (database or "").strip()
    if value in {"", "(default)", "%28default%29"}:
        return "(default)"
    decoded = unquote(value).strip()
    return decoded or "(default)"


def _client_kwargs(*, project: str, database: str) -> dict[str, str]:
    kwargs = {"project": project}
    if database != "(default)":
        kwargs["database"] = database
    return kwargs


class SiteKnowledgeStore(Protocol):
    backend_name: str

    async def initialize(self) -> None: ...
    async def list(self) -> list[SiteKnowledge]: ...
    async def save(self, item: SiteKnowledge) -> None: ...
    async def replace(self, items: list[SiteKnowledge]) -> None: ...
    async def clear(self) -> None: ...
    async def close(self) -> None: ...


class JsonSiteKnowledgeStore:
    backend_name = "json"

    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)

    async def list(self) -> list[SiteKnowledge]:
        if not await asyncio.to_thread(self.path.exists):
            return []
        raw = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
        payload = json.loads(raw)
        return [SiteKnowledge.model_validate(item) for item in payload]

    async def save(self, item: SiteKnowledge) -> None:
        items = await self.list()
        normalized = item.assertion.strip().casefold()
        items = [existing for existing in items if existing.assertion.strip().casefold() != normalized]
        items.append(item)
        await self.replace(items)

    async def replace(self, items: list[SiteKnowledge]) -> None:
        payload = json.dumps([item.model_dump(mode="json") for item in items], indent=2)

        def _write() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.path)

        await asyncio.to_thread(_write)

    async def clear(self) -> None:
        await asyncio.to_thread(self.path.unlink, missing_ok=True)

    async def close(self) -> None:
        return None


class FirestoreSiteKnowledgeStore:
    backend_name = "firestore"

    def __init__(
        self,
        *,
        project: str,
        namespace: str,
        collection: str = "plant_site_knowledge",
        database: str = "(default)",
    ) -> None:
        if not project:
            raise ValueError("GOOGLE_CLOUD_PROJECT is required for Firestore site knowledge")
        if "/" in collection:
            raise ValueError("Site knowledge collection must be a collection name")
        self.project = project
        self.namespace = _safe_segment(namespace, "default")
        self.collection = collection
        self.database = _normalize_database(database)
        self._client = None
        self._doc = None
        self._server_timestamp = None

    async def initialize(self) -> None:
        try:
            from google.cloud import firestore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("google-cloud-firestore is required for hosted site knowledge") from exc
        self._client = firestore.AsyncClient(**_client_kwargs(project=self.project, database=self.database))
        self._doc = self._client.collection(self.collection).document(self.namespace)
        self._server_timestamp = firestore.SERVER_TIMESTAMP

    async def list(self) -> list[SiteKnowledge]:
        if self._doc is None:
            raise RuntimeError("Site knowledge store is not initialized")
        snapshot = await self._doc.get()
        if not snapshot.exists:
            return []
        payload = snapshot.to_dict() or {}
        return [SiteKnowledge.model_validate(item) for item in payload.get("items", [])]

    async def save(self, item: SiteKnowledge) -> None:
        items = await self.list()
        normalized = item.assertion.strip().casefold()
        items = [existing for existing in items if existing.assertion.strip().casefold() != normalized]
        items.append(item)
        await self.replace(items)

    async def replace(self, items: list[SiteKnowledge]) -> None:
        if self._doc is None:
            raise RuntimeError("Site knowledge store is not initialized")
        await self._doc.set(
            {
                "namespace": self.namespace,
                "updated_at": self._server_timestamp,
                "items": [item.model_dump(mode="json") for item in items],
            }
        )

    async def clear(self) -> None:
        if self._doc is None:
            raise RuntimeError("Site knowledge store is not initialized")
        await self._doc.delete()

    async def close(self) -> None:
        if self._client is None:
            return
        close = getattr(self._client, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result


def create_site_knowledge_store(*, state_store, namespace: str) -> SiteKnowledgeStore:
    """Match knowledge durability to a real Firestore Field Ops store.

    Unit-test doubles can intentionally report ``backend_name='firestore'`` to exercise
    the Field Ops fail-closed path without implementing Firestore configuration fields.
    Those doubles are not silently treated as a production Firestore client. The actual
    Firestore state store created by the application always exposes ``project`` and
    ``database``; if those values are present, knowledge uses the same durable backend.
    """
    if (
        getattr(state_store, "backend_name", "") == "firestore"
        and hasattr(state_store, "project")
        and hasattr(state_store, "database")
    ):
        return FirestoreSiteKnowledgeStore(
            project=state_store.project,
            namespace=namespace,
            database=state_store.database,
        )
    if getattr(state_store, "backend_name", "") == "json" and hasattr(state_store, "path"):
        state_path = Path(state_store.path)
        return JsonSiteKnowledgeStore(
            state_path.with_name(f"{state_path.stem}-site-knowledge.json")
        )
    return JsonSiteKnowledgeStore(Path("/tmp/plant-debugger-site-knowledge.json"))


def new_operator_assertion(*, assertion: str, asset_id: str | None, session_id: str | None) -> SiteKnowledge:
    text = " ".join(assertion.strip().split())
    if len(text) < 3:
        raise ValueError("Site knowledge assertion is too short")
    if len(text) > 500:
        raise ValueError("Site knowledge assertion exceeds 500 characters")
    return SiteKnowledge(
        knowledge_id=f"knowledge-{uuid.uuid4().hex[:12]}",
        assertion=text,
        asset_id=asset_id,
        source="operator_assertion",
        learned_at=datetime.now(UTC),
        learned_in_session=session_id,
    )
