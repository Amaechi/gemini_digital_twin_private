from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class IncidentStatus(StrEnum):
    DETECTED = "DETECTED"
    INVESTIGATING = "INVESTIGATING"
    ACTING = "ACTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"


class EventType(StrEnum):
    ALERT = "ALERT"
    OBSERVE = "OBSERVE"
    RUNBOOK = "RUNBOOK"
    HYPOTHESIS = "HYPOTHESIS"
    ACTION_REQUEST = "ACTION_REQUEST"
    ACTION_RESULT = "ACTION_RESULT"
    VERIFY = "VERIFY"
    ERROR = "ERROR"
    RESOLVE = "RESOLVE"
    ESCALATE = "ESCALATE"


class AlertEvent(BaseModel):
    event_id: str
    asset_id: str
    detected_at: datetime
    rule: str
    snapshot: dict[str, float]
    trace_id: str


class IncidentRecord(BaseModel):
    incident_id: str
    asset_id: str
    trigger_event_id: str
    trigger_payload: dict[str, Any]
    status: IncidentStatus = IncidentStatus.DETECTED
    current_step: int = 0
    control_action_count: int = 0
    started_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    health_before: float | None = None
    health_after: float | None = None
    final_summary: str | None = None
    trace_id: str


class IncidentEvent(BaseModel):
    event_id: str
    incident_id: str
    ts: datetime
    type: EventType
    step_no: int
    payload: dict[str, Any]
    trace_id: str


class TelemetryPoint(BaseModel):
    ts: datetime
    flow: float
    pressure_in: float
    pressure_out: float
    differential_pressure: float
    vibration: float
    motor_current: float
    temperature: float
    health_score: float


class TelemetryResponse(BaseModel):
    asset_id: str
    scenario_id: str | None
    status: str
    controls: dict[str, Any]
    asset_state: dict[str, Any]
    baseline: dict[str, float]
    samples: list[TelemetryPoint]


class PublicSimulatorState(BaseModel):
    asset_id: str
    scenario_id: str | None
    status: str
    scenario_started_at: datetime | None
    fault_scheduled_at: datetime | None
    anomaly_event_published: bool
    controls: dict[str, Any]
    action_count: int
    standby_ready: bool


class StartScenarioRequest(BaseModel):
    scenario: Literal["hero", "cavitation_recovery", "bearing_failover"] = "hero"
    seed: int = Field(default=20260821, ge=0)
    fault_delay_s: float = Field(default=3.0, ge=0.0, le=30.0)
    severity: float = Field(default=1.0, ge=0.5, le=1.25)
    publish_anomaly: bool = True


class VerifyResult(BaseModel):
    resolved: bool
    health_before: float
    health_after: float
    stable_samples: int
    thresholds: dict[str, float]
    before: dict[str, float]
    after: dict[str, float]
    deltas: dict[str, float]
    summary: str


class GuardResult(BaseModel):
    approved: bool
    status: Literal["APPROVED", "REJECTED"]
    action_id: str | None = None
    normalized_action: dict[str, Any] | None = None
    reason: str
    execution_result: dict[str, Any] | None = None


class IncidentRunResult(BaseModel):
    incident_id: str
    status: IncidentStatus
    duplicate: bool = False
    summary: str | None = None


class DashboardState(BaseModel):
    service: dict[str, Any]
    simulator: PublicSimulatorState
    telemetry: TelemetryResponse
    incident: IncidentRecord | None
    events: list[IncidentEvent]
