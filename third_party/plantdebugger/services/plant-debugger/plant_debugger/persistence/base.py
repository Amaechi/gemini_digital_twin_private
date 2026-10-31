from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..schemas import AlertEvent, IncidentEvent, IncidentRecord, IncidentStatus


class IncidentStore(ABC):
    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def get_or_create_incident(self, event: AlertEvent) -> tuple[IncidentRecord, bool]: ...

    @abstractmethod
    async def get_incident(self, incident_id: str) -> IncidentRecord | None: ...

    @abstractmethod
    async def get_latest_incident(self) -> IncidentRecord | None: ...

    @abstractmethod
    async def update_incident(self, incident_id: str, **fields: Any) -> IncidentRecord: ...

    @abstractmethod
    async def append_event(self, event: IncidentEvent) -> None: ...

    @abstractmethod
    async def list_events(self, incident_id: str) -> list[IncidentEvent]: ...

    @abstractmethod
    async def list_incidents(self, limit: int = 20) -> list[IncidentRecord]: ...

    @abstractmethod
    async def clear(self) -> None: ...

    async def set_status(self, incident_id: str, status: IncidentStatus, **fields: Any) -> IncidentRecord:
        return await self.update_incident(incident_id, status=status, **fields)
