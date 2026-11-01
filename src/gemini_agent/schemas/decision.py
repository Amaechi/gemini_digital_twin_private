# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file services/plant-debugger/plant_debugger/agent/schemas.py @ d69507a. Modified by GeoAgent team,
# 2026-11-01: incident kinds/tools replaced by the GeoAgent phase/step kinds of spec §5.3; added
# Decision Records (AG-07), option choices, missing-input requests (AG-05) and run status enums.
"""Typed decisions exchanged between the workflow runner and the decision engines (spec §5.3).

The runner owns the phase order (P0–P10); an engine (deterministic policy or an LLM agent) only
chooses the next step(s) inside a phase, or one option of a Decision Record. Engines never
produce engineering numbers: every number comes from a tool result (CLAUDE.md golden rule 1).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from gemini_agent.schemas.common import StrictModel

RATIONALE_MAX_WORDS = 60
"""Spec §5.3: ``rationale`` ≤ 60 words."""

MAX_STEPS_PER_DECISION = 3
"""Spec §5.3: a decision proposes at most three steps."""


class Phase(StrEnum):
    """Workflow phases of the deterministic skeleton (spec §5.2)."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
    P5 = "P5"
    P6 = "P6"
    P7 = "P7"
    P8 = "P8"
    P9 = "P9"
    P10 = "P10"


PHASE_TITLES: dict[Phase, str] = {
    Phase.P0: "Intake",
    Phase.P1: "Design basis",
    Phase.P2: "Resource",
    Phase.P3: "Concepts",
    Phase.P4: "Model gaps",
    Phase.P5: "Screening",
    Phase.P6: "Optimisation",
    Phase.P7: "Verification",
    Phase.P8: "Uncertainty",
    Phase.P9: "Review",
    Phase.P10: "Report",
}


class Role(StrEnum):
    """Agent roles (spec §5.1)."""

    LEAD_ENGINEER = "lead_engineer"
    REQUIREMENTS = "requirements"
    SUBSURFACE = "subsurface"
    SYSTEM_ARCHITECT = "system_architect"
    MODEL_BUILDER = "model_builder"
    ECONOMICS = "economics"
    REVIEWER = "reviewer"
    REPORTER = "reporter"


StepKind = Literal[
    "call_tool",
    "request_input",
    "propose_candidates",
    "generate_model",
    "run_optimisation",
    "verify",
    "delegate",
    "finalise",
    "escalate",
    "refuse",
]
"""Spec §5.3 step kinds plus ``refuse`` (AG-06 off-topic refusal, P0 only)."""

KIND_TOOLS: dict[str, frozenset[str] | None] = {
    "request_input": frozenset({"request_missing_input"}),
    "propose_candidates": frozenset({"generate_candidates"}),
    "generate_model": frozenset(
        {"read_reference_card", "write_model_file", "run_pytest", "register_model"}
    ),
    "run_optimisation": frozenset({"optimise_design"}),
    "verify": frozenset({"verify_design", "design_guard"}),
    "delegate": None,
    "finalise": None,
    "escalate": None,
    "refuse": None,
}
"""Kind ↔ tool rule (Plant Debugger ``validate_kind_tool``). ``None``: the step takes no tool.
``call_tool`` accepts any tool except the ones reserved for another kind."""

RESERVED_TOOLS: frozenset[str] = frozenset(
    tool for tools in KIND_TOOLS.values() if tools is not None for tool in tools
)

DecisionSource = Literal["policy", "llm", "policy_fallback", "workflow", "user", "assumption"]
"""Who made a decision: ``policy`` (PolicyEngine run), ``llm`` (validated LLM output),
``policy_fallback`` (LLM failed after ≤ 2 retries, AG-03), ``workflow`` (deterministic runner
step, spec §5.4.6), ``user`` (explicit answer), ``assumption`` (labelled default, AG-05)."""

ArgValue = str | int | float | bool | None
"""Flat tool arguments only (spec §5.4.4)."""


class RunStatus(StrEnum):
    """Lifecycle of one workflow run."""

    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    REFUSED = "refused"


class DesignVerdict(StrEnum):
    """Verdict copied from the verifier; only ``DesignVerifier`` may produce ACCEPTED (AG-11)."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNVERIFIED = "UNVERIFIED"


def _word_count(text: str) -> int:
    return len(text.split())


_NUMBER_WITH_UNIT = re.compile(
    r"(?<![\w.\-])\d+(?:[.,]\d+)?\s*(?:°C|degC|K|kW|MW|GW|kWh|MWh|GWh|m3/h|m³/h|m3|m³|bar|barg|"
    r"kPa|Pa|%|USD|EUR|€|km|m|t/yr|kg|yr|years?)(?![\w/])"
)
"""A quantity with a unit, e.g. '55 degC', '300 m3/h', '12 %'."""


def reject_llm_quantities(text: str) -> str:
    """Refuse quantities in free text that ends up in Decision Records (AG-02).

    Numbers in reports come from tool outputs only; rationales cite option or evidence ids.
    """
    match = _NUMBER_WITH_UNIT.search(text)
    if match:
        raise ValueError(
            f"do not state quantities ('{match.group(0)}'); cite option or evidence ids instead"
        )
    return text


class NextStep(StrictModel):
    """One step an engine proposes inside the current phase (spec §5.3)."""

    kind: StepKind
    tool: str | None = Field(
        default=None, max_length=64, description="Must be in this agent's tool list."
    )
    args: dict[str, ArgValue] = Field(
        default_factory=dict, description="Flat arguments, validated against the tool's schema."
    )
    rationale: str = Field(min_length=1, max_length=600, description="≤ 60 words.")
    expected_outcome: str = Field(default="", max_length=300)

    @field_validator("rationale")
    @classmethod
    def _short_rationale(cls, text: str) -> str:
        if _word_count(text) > RATIONALE_MAX_WORDS:
            raise ValueError(f"rationale must be <= {RATIONALE_MAX_WORDS} words")
        return text

    @model_validator(mode="after")
    def _check_kind_tool(self) -> NextStep:
        if self.kind == "call_tool":
            if not self.tool:
                raise ValueError("call_tool steps need a tool")
            if self.tool in RESERVED_TOOLS:
                raise ValueError(f"tool '{self.tool}' needs its own step kind, not call_tool")
            return self
        allowed = KIND_TOOLS[self.kind]
        if allowed is None:
            if self.tool is not None:
                raise ValueError(f"{self.kind} steps take no tool, got '{self.tool}'")
        elif self.tool not in allowed:
            raise ValueError(f"{self.kind} steps must use one of {sorted(allowed)}")
        return self


class Decision(StrictModel):
    """Structured output of an engine for one decision round (spec §5.3)."""

    phase: Phase
    steps: list[NextStep] = Field(min_length=1, max_length=MAX_STEPS_PER_DECISION)
    open_questions: list[str] = Field(default_factory=list, max_length=5)


class ToolBrief(StrictModel):
    """What an agent sees of one tool: name, one sentence, flat argument names (spec §5.4.4)."""

    name: str
    description: str
    args: dict[str, str] = Field(default_factory=dict, description="arg name -> type/default")


class InputRequest(StrictModel):
    """A pending ``request_missing_input`` question (AG-05)."""

    request_id: str
    phase: Phase
    question: str
    assumption_id: str | None = Field(
        default=None, description="Register entry whose default applies if no answer is given."
    )
    default: JsonValue = None
    unit: str | None = None


class InputAnswer(StrictModel):
    """Answer to an ``InputRequest``: explicit user answer or labelled assumption."""

    request_id: str
    assumption_id: str | None = None
    answer: JsonValue
    source: Literal["user", "assumption"]
    label: str = Field(description="Human-readable record, e.g. 'DB-31 = 55.0 degC (assumed)'.")


class DecisionContext(StrictModel):
    """Compact view of the run that an engine decides on (Plant Debugger ``DecisionContext``)."""

    run_id: str
    phase: Phase
    phase_title: str
    role: Role
    objective: str
    user_prompt: str
    in_scope: bool = True
    scope_reason: str = ""
    allowed_tools: list[ToolBrief]
    required_tools_remaining: list[str] = Field(default_factory=list)
    completed_tools: list[str] = Field(default_factory=list)
    default_args: dict[str, dict[str, ArgValue]] = Field(default_factory=dict)
    open_gaps: list[str] = Field(default_factory=list)
    gap_questions: dict[str, str] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    loop_count: int = 0
    budget_left: dict[str, int] = Field(default_factory=dict)


DR_IDS = Literal[
    "DR-01", "DR-02", "DR-03", "DR-04", "DR-05", "DR-06", "DR-07", "DR-08", "DR-09", "DR-10"
]

DR_TITLES: dict[str, str] = {
    "DR-01": "Number of doublets",
    "DR-02": "Well depth",
    "DR-03": "Heat top-up technology",
    "DR-04": "Cooling technology mix",
    "DR-05": "Storage yes/no",
    "DR-06": "Maintenance windows",
    "DR-07": "Gas handling (degas + CHP vs pressurised reinjection)",
    "DR-08": "Return temperature",
    "DR-09": "Materials",
    "DR-10": "Final selection",
}
"""AG-07 Decision Records."""


class DecisionOption(StrictModel):
    """One option offered by the workflow for a Decision Record."""

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$", max_length=64)
    label: str
    evidence_ids: list[str] = Field(default_factory=list, description="Tool-result IDs.")
    recommended: bool = Field(
        default=False, description="Set by a deterministic tool ranking or the register default."
    )
    eligible: bool = Field(default=True, description="False: shown for transparency only.")
    notes: str = ""


class DecisionQuestion(StrictModel):
    """A bounded choice the workflow offers an engine (spec §4.1 'LLMs choose within options')."""

    dr_id: DR_IDS
    title: str
    question: str
    phase: Phase
    role: Role
    options: list[DecisionOption] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_options(self) -> DecisionQuestion:
        ids = [option.id for option in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate option ids in {self.dr_id}: {ids}")
        if not any(option.eligible for option in self.options):
            raise ValueError(f"{self.dr_id} has no eligible option")
        return self


class RejectedOption(StrictModel):
    """Why an option was not chosen."""

    option_id: str
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def _no_quantities(cls, text: str) -> str:
        return reject_llm_quantities(text)


class OptionChoice(StrictModel):
    """Structured output of an engine for a Decision Record question."""

    dr_id: DR_IDS
    option_id: str
    rationale: str = Field(min_length=1, max_length=600, description="≤ 60 words.")
    rejected: list[RejectedOption] = Field(default_factory=list)

    @field_validator("rationale")
    @classmethod
    def _short_rationale(cls, text: str) -> str:
        if _word_count(text) > RATIONALE_MAX_WORDS:
            raise ValueError(f"rationale must be <= {RATIONALE_MAX_WORDS} words")
        return reject_llm_quantities(text)


class DecisionRecord(StrictModel):
    """AG-07 Decision Record: question, options, evidence, choice, why others were rejected."""

    dr_id: DR_IDS
    title: str
    run_id: str
    phase: Phase
    role: Role
    question: str
    options: list[DecisionOption]
    evidence_ids: list[str]
    choice: str
    choice_label: str
    rationale: str
    rejected: list[RejectedOption]
    decision_source: DecisionSource
    stub_evidence: bool = Field(
        default=False, description="True if any evidence came from a stub tool (not final)."
    )
