from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from ..schemas import PublicSimulatorState, TelemetryResponse


class FieldOpsStatus(StrEnum):
    ARMED = "ARMED"
    INSPECTION_DISPATCHED = "INSPECTION_DISPATCHED"
    ASSET_VERIFIED = "ASSET_VERIFIED"
    ANALYZING_EVIDENCE = "ANALYZING_EVIDENCE"
    ACTION_DISPATCHED = "ACTION_DISPATCHED"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"


class FieldTaskKind(StrEnum):
    INSPECTION = "INSPECTION"
    MANUAL_ACTION = "MANUAL_ACTION"


class FieldTaskStatus(StrEnum):
    DISPATCHED = "DISPATCHED"
    ASSET_VERIFIED = "ASSET_VERIFIED"
    EVIDENCE_SUBMITTED = "EVIDENCE_SUBMITTED"
    COMPLETED = "COMPLETED"


class FieldOpsEventType(StrEnum):
    SCENARIO_STARTED = "SCENARIO_STARTED"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    PUBSUB_ANOMALY_RECEIVED = "PUBSUB_ANOMALY_RECEIVED"
    ADK_WORKFLOW_STARTED = "ADK_WORKFLOW_STARTED"
    ADK_WORKFLOW_PAUSED = "ADK_WORKFLOW_PAUSED"
    ADK_WORKFLOW_RESUMED = "ADK_WORKFLOW_RESUMED"
    ADK_WORKFLOW_COMPLETED = "ADK_WORKFLOW_COMPLETED"
    PLAN_SELECTED = "PLAN_SELECTED"
    HYPOTHESIS = "HYPOTHESIS"
    FIELD_TASK_DISPATCHED = "FIELD_TASK_DISPATCHED"
    QR_VERIFIED = "QR_VERIFIED"
    IMAGE_EVIDENCE_RECEIVED = "IMAGE_EVIDENCE_RECEIVED"
    VISION_ASSESSMENT = "VISION_ASSESSMENT"
    ACTION_EVIDENCE_RECEIVED = "ACTION_EVIDENCE_RECEIVED"
    FIELD_ACTION_CONFIRMED = "FIELD_ACTION_CONFIRMED"
    CONTROL_MIRRORED = "CONTROL_MIRRORED"
    VERIFY = "VERIFY"
    KNOWLEDGE_LEARNED = "KNOWLEDGE_LEARNED"
    KNOWLEDGE_USED = "KNOWLEDGE_USED"
    KNOWLEDGE_RESET = "KNOWLEDGE_RESET"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"


class HypothesisScore(BaseModel):
    cause: str
    probability: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class SiteKnowledge(BaseModel):
    knowledge_id: str
    assertion: str = Field(min_length=3, max_length=500)
    asset_id: str | None = None
    source: Literal["operator_assertion"] = "operator_assertion"
    learned_at: datetime
    learned_in_session: str | None = None
    use_count: int = Field(default=0, ge=0)
    last_used_at: datetime | None = None


class VisionAssessment(BaseModel):
    asset_visible: bool = True
    asset_label: str | None = None
    visible_active_leak: bool = False
    visible_surface_damage: bool = False
    wetness_or_pooling: bool = False
    differential_pressure_indicator_visible: bool = False
    differential_pressure_state: Literal["NORMAL", "HIGH", "UNCERTAIN", "NOT_APPLICABLE"] = "NOT_APPLICABLE"
    valve_position: Literal["OPEN", "CLOSED", "UNCERTAIN", "NOT_APPLICABLE"] = "NOT_APPLICABLE"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    observations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    hypothesis_effect: str = "inconclusive"
    summary: str
    source: str = "unknown"


class FieldTask(BaseModel):
    task_id: str
    kind: FieldTaskKind
    status: FieldTaskStatus = FieldTaskStatus.DISPATCHED
    asset_id: str
    asset_name: str
    title: str
    reason: str
    instructions: list[str]
    safety: list[str]
    required_qr_payload: str | None = None
    qr_verified_at: datetime | None = None
    question: str | None = None
    action: dict[str, Any] | None = None
    evidence_id: str | None = None
    evidence_url: str | None = None
    evidence_object: str | None = None
    evidence_uri: str | None = None
    evidence_content_type: str | None = None
    evidence_filename: str | None = None
    evidence_size: int | None = None
    evidence_sha256: str | None = None
    vision_assessment: VisionAssessment | None = None
    action_evidence_required: bool = False
    action_evidence_verified: bool = False
    created_at: datetime
    updated_at: datetime


class FieldOpsEvent(BaseModel):
    event_id: str
    ts: datetime
    type: FieldOpsEventType
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)


class FieldOpsSession(BaseModel):
    session_id: str
    scenario_id: str
    status: FieldOpsStatus
    started_at: datetime
    updated_at: datetime
    current_task_id: str | None = None
    tasks: list[FieldTask] = Field(default_factory=list)
    events: list[FieldOpsEvent] = Field(default_factory=list)
    hypotheses: list[HypothesisScore] = Field(default_factory=list)
    planning_source: str | None = None
    orchestration_source: str | None = None
    vision_source: str | None = None
    adk_session_id: str | None = None
    adk_invocation_id: str | None = None
    adk_interrupt_id: str | None = None
    adk_node_path: str | None = None
    agent_steps: int = 0
    verification_failures: int = 0
    human_decisions: int = 0
    operator_executions: int = 0
    pre_action_telemetry: dict[str, Any] | None = None
    verify_not_before: datetime | None = None
    final_summary: str | None = None


class FieldOpsStartRequest(BaseModel):
    seed: int = Field(default=20260821, ge=0)
    fault_delay_s: float = Field(default=3.0, ge=0.0, le=30.0)
    severity: float = Field(default=1.0, ge=0.5, le=1.25)
    publish_anomaly: bool = Field(default=True)


class QrVerificationRequest(BaseModel):
    qr_payload: str = Field(min_length=1, max_length=512)


class ManualActionConfirmation(BaseModel):
    completed: bool = True


class SiteKnowledgeAssertionRequest(BaseModel):
    assertion: str = Field(min_length=3, max_length=500)
    asset_id: str | None = Field(default=None, max_length=64)


class FieldOpsDashboardState(BaseModel):
    service: dict[str, Any]
    simulator: PublicSimulatorState
    telemetry: TelemetryResponse
    session: FieldOpsSession | None


class FieldControlAction(BaseModel):
    action: Literal["open_bypass_pct", "set_rpm_delta_pct", "switch_standby"] = Field(
        description="A bounded physical action available to the field operator."
    )
    delta_pct: float | None = Field(
        default=None,
        description="Percentage delta for bypass or RPM actions. Not used for switch_standby.",
    )
    enabled: bool | None = Field(
        default=None,
        description="Optional switch state for switch_standby.",
    )


class FieldPlan(BaseModel):
    assessment: str = Field(
        description="Concise evidence-based assessment. Do not expose private chain-of-thought."
    )
    hypotheses: list[HypothesisScore]
    selected_tool: Literal["request_field_image", "request_manual_action", "escalate"]
    task_kind: Literal["INSPECTION", "MANUAL_ACTION", "ESCALATE"]
    asset_id: str
    asset_name: str
    title: str
    reason: str
    expected_observation: str = Field(
        default="",
        description="What observation would make this step informative or successful.",
    )
    question: str | None = None
    instructions: list[str] = Field(default_factory=list)
    safety: list[str] = Field(default_factory=list)
    action: FieldControlAction | dict[str, Any] | None = Field(
        default=None,
        description="Exact bounded control parameters when request_manual_action is selected.",
    )
    action_evidence_required: bool = Field(
        default=False,
        description=(
            "Whether the operator must return image evidence before this manual action can be confirmed. "
            "Use this only when the requested physical end state is visually verifiable."
        ),
    )


class FieldPlanningContext(BaseModel):
    trigger: Literal["anomaly", "vision_result", "verification_failed"]
    step_no: int = Field(ge=1)
    remaining_actions: int = Field(ge=0)
    telemetry: TelemetryResponse
    hypotheses: list[HypothesisScore] = Field(default_factory=list)
    completed_tasks: list[dict[str, Any]] = Field(default_factory=list)
    latest_vision: VisionAssessment | None = None
    latest_verification: dict[str, Any] | None = None
    site_knowledge: list[SiteKnowledge] = Field(default_factory=list)
    available_tools: list[dict[str, Any]] = Field(default_factory=list)


class FieldPlanner(Protocol):
    source: str

    async def plan_next(self, context: FieldPlanningContext) -> FieldPlan: ...


class VisionAnalyzer(Protocol):
    source: str

    async def analyze(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        filename: str,
        question: str,
    ) -> VisionAssessment: ...
