from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Hypothesis(BaseModel):
    cause: Literal["cavitation", "valve_obstruction", "bearing_degradation", "unknown"]
    rank: int = Field(ge=1, le=4)
    supporting_evidence: list[str] = Field(default_factory=list, max_length=5)
    contradicting_evidence: list[str] = Field(default_factory=list, max_length=5)


class NextStep(BaseModel):
    kind: Literal["observe", "act", "resolve", "escalate"]
    tool: Literal[
        "get_recent_telemetry",
        "search_runbook",
        "request_control_action",
        "wait_and_verify",
        "escalate",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_observation: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_kind_tool(self) -> "NextStep":
        allowed = {
            "observe": {"get_recent_telemetry", "search_runbook", "wait_and_verify"},
            "act": {"request_control_action"},
            "resolve": {"wait_and_verify"},
            "escalate": {"escalate"},
        }
        if self.tool not in allowed[self.kind]:
            raise ValueError(f"{self.kind} steps cannot use {self.tool}")
        return self


class NextStepDecision(BaseModel):
    assessment: str = Field(min_length=1, max_length=800)
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=4)
    next_step: NextStep


class DecisionContext(BaseModel):
    incident: dict[str, Any]
    telemetry: dict[str, Any]
    history: list[dict[str, Any]]
    runbook_snippets: list[dict[str, str]]
    safety: dict[str, Any]
