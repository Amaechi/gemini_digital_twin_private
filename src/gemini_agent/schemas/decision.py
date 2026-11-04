# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file services/plant-debugger/plant_debugger/agent/schemas.py @ d69507a. Modified by GeoAgent team,
# 2026-11-01: incident kinds/tools replaced by the GeoAgent phase/step kinds of spec §5.3; added
# Decision Records (AG-07), option choices, missing-input requests (AG-05) and run status enums.
# 2026-11-02 (L-1 follow-up): pre-validation hook for step kinds; DR criteria and evidence keys.
# 2026-11-02 (pre-L-2): DR-11 backup capacity (OPT-ROBUST-BACKUP).
# 2026-11-03 (review loop): DR-12 user review; ReviewQuestion / LeverChoice (objection → lever).
"""Typed decisions exchanged between the workflow runner and the decision engines (spec §5.3).

The runner owns the phase order (P0–P10); an engine (deterministic policy or an LLM agent) only
chooses the next step(s) inside a phase, or one option of a Decision Record. Engines never
produce engineering numbers: every number comes from a tool result (CLAUDE.md golden rule 1).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, JsonValue, ValidationInfo, field_validator, model_validator

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

NO_TOOL_KINDS: frozenset[str] = frozenset(
    kind for kind, tools in KIND_TOOLS.items() if tools is None
)
"""Kinds whose meaning does not come from a tool (finalise, escalate, delegate, refuse)."""

PRE_VALIDATE_HOOK = "decision_pre_validate"
"""Pydantic validation-context key: a callable ``(raw Decision data) -> raw data`` that runs before
``Decision`` is validated. The LLM engine sets it (``agents.engine.coerce_step_kinds``) to repair
step kinds from tool names before the kind ↔ tool rule is checked; direct construction (policy,
tests) has no context and is unchanged."""

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


_LLM_NUMBER = re.compile(
    # a digit run that does not start inside an identifier (DR-01, TR-003, C1-3d, depth-2500m,
    # §10.1, P10, CO2) — with or without a unit ('45 USD/MWh', '120C', '50mD', 'COP 4.2', '-5')
    r"(?<![\w.§\-])-?\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?"
    # ... unless it starts an option id such as '3-doublets'
    r"(?!\d|[.,]\d|-[^\W\d_])"
)
"""A number an LLM wrote in free text (not a digit inside an identifier)."""


_KEY_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?![A-Za-z0-9_])")
"""A snake_case identifier in free text — how a rationale cites a tool-result key or tool."""


def key_tokens(text: str, ignore: Iterable[str] = ()) -> list[str]:
    """Snake_case identifiers in ``text`` (the evidence keys and tool names it cites).

    ``ignore`` strings (evidence ids such as ``config:design_basis.yaml#DB-01``) are removed
    first, so a reference is not read as a key.
    """
    for item in sorted(ignore, key=len, reverse=True):
        if item:
            text = text.replace(item, " ")
    return _KEY_TOKEN.findall(text)


def names_key(text: str, key: str) -> bool:
    """Return True if ``text`` names ``key`` as a whole word (``passed`` is not ``bypassed``)."""
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", text) is not None


def states_numbers(text: str) -> bool:
    """Return True if LLM free text states a number outside an identifier (AG-02)."""
    return _LLM_NUMBER.search(text) is not None


def reject_llm_quantities(text: str) -> str:
    """Refuse numbers in LLM free text that ends up in run artefacts (AG-02, golden rule 1).

    Numbers in Decision Records and run summaries come from tool outputs only; LLM text cites
    option, evidence, DR or assumption ids instead. Digits inside such ids are allowed; numbers
    spelled out in words are not detected (the prompts forbid them).
    """
    match = _LLM_NUMBER.search(text)
    if match:
        token = text[match.start() :].split()[0]
        raise ValueError(
            f"do not state numbers or quantities ('{token}'); cite option or evidence ids instead"
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
    def _no_quantities_in_escalation(self) -> NextStep:
        # An escalate rationale becomes the run summary's escalation reason (AG-02). An LLM
        # request_input question is checked by the runner, which uses it only as a fallback.
        if self.kind == "escalate":
            reject_llm_quantities(self.rationale)
        return self

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

    @model_validator(mode="before")
    @classmethod
    def _pre_validate_hook(cls, data: Any, info: ValidationInfo) -> Any:
        context = info.context
        hook = context.get(PRE_VALIDATE_HOOK) if isinstance(context, Mapping) else None
        return hook(data) if callable(hook) else data


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


class LeverBrief(StrictModel):
    """One bounded what-if lever as the review agent sees it (``tools.whatif.LEVERS``)."""

    id: str
    kind: Literal["float", "int", "choice", "flag", "candidate"]
    description: str
    unit: str = ""
    choices: list[str] = Field(default_factory=list)
    bound: str = ""
    adoptable: bool = Field(
        default=True,
        description="False: shown in the what-if only (a scenario lever or a TL-13 size "
        "variable); the run cannot adopt it.",
    )


class CandidateBrief(StrictModel):
    """A screened candidate the ``concept`` lever may re-pick (no KPIs: ids and geometry only)."""

    id: str
    concept: str
    doublets: int
    depth_m: float | None = None
    description: str = ""


class ReviewQuestion(StrictModel):
    """The user's objection to the recommended design, to be mapped to one lever (review loop).

    The workflow builds it from the run state; the engine answers with a :class:`LeverChoice`.
    Numbers in ``objection`` are the user's own words; the engine copies, never computes.
    """

    run_id: str
    phase: Phase = Phase.P9
    role: Role = Role.LEAD_ENGINEER
    round: int = Field(ge=1, description="Objection round in this review (max 3).")
    objection: str = Field(min_length=1, max_length=600)
    selected: CandidateBrief
    selected_returns_degc: dict[str, float] = Field(
        default_factory=dict, description="heating_return_degc / cooling_return_degc."
    )
    robust_backup_installed: bool | None = Field(
        default=None, description="Whether the design carries the TL-15 robust backups (DR-11)."
    )
    levers: list[LeverBrief] = Field(min_length=1)
    candidates: list[CandidateBrief] = Field(
        default_factory=list, description="Screened candidates the concept lever may pick."
    )
    feedback: str = Field(default="", description="Why the previous round did not match.")

    def lever(self, lever_id: str | None) -> LeverBrief | None:
        """Return the offered lever with this id (None if not offered)."""
        return next((lever for lever in self.levers if lever.id == lever_id), None)


class LeverChoice(StrictModel):
    """Structured output of the review mapping: exactly one lever + value, or no match.

    ``lever`` None means no offered lever expresses the objection (the user is asked again).
    ``value`` is text the what-if parses (``tools.whatif.parse_levers``): the user's own number
    or choice, a candidate id, ``on``/``off`` — never a quantity the engine worked out.
    """

    lever: str | None = Field(default=None, max_length=64)
    value: str | None = Field(default=None, max_length=64)
    rationale: str = Field(min_length=1, max_length=600, description="≤ 60 words, no numbers.")

    @field_validator("rationale")
    @classmethod
    def _short_rationale(cls, text: str) -> str:
        if _word_count(text) > RATIONALE_MAX_WORDS:
            raise ValueError(f"rationale must be <= {RATIONALE_MAX_WORDS} words")
        return reject_llm_quantities(text)

    @model_validator(mode="after")
    def _value_with_lever(self) -> LeverChoice:
        if self.lever is not None and (self.value is None or not self.value.strip()):
            raise ValueError(f"lever {self.lever} needs a value")
        if self.lever is None and self.value is not None:
            raise ValueError("value without a lever; set lever null for 'no lever matches'")
        return self


_FLAG_WORDS = {"on", "off", "true", "false", "yes", "no", "1", "0"}
INT_STEPS: dict[str, int] = {"more": 1, "fewer": -1, "less": -1}
"""Integer-lever values an engine may return instead of a count (golden rule 1: the runner steps
the selected design's count deterministically, ``runner.review.resolve_value``)."""


def check_lever_choice(question: ReviewQuestion, choice: LeverChoice) -> str | None:
    """Return an error message for the engine, or None if the lever choice is acceptable.

    The lever must be one the question offers and the value must parse for its kind (number,
    integer, one of the choices, on/off, or a candidate id / concept of ``question.candidates``).
    Bounds (register range, DesignGuard) are checked by the what-if itself.
    """
    if choice.lever is None:
        return None
    lever = question.lever(choice.lever)
    if lever is None:
        return f"lever must be one of the offered ids {[item.id for item in question.levers]}"
    value = (choice.value or "").strip()
    if lever.kind == "float":
        try:
            float(value)
        except ValueError:
            return f"{lever.id} needs a number ({lever.unit or 'unitless'}), got '{value}'"
    elif lever.kind == "int":
        if value.lower() not in INT_STEPS and not re.fullmatch(r"[+-]?\d+", value):
            return f"{lever.id} needs an integer (or more / fewer), got '{value}'"
    elif lever.kind == "choice":
        if value.lower() not in {c.lower() for c in lever.choices}:
            return f"{lever.id} must be one of {lever.choices}, got '{value}'"
    elif lever.kind == "flag":
        if value.lower() not in _FLAG_WORDS:
            return f"{lever.id} must be on or off, got '{value}'"
    elif lever.kind == "candidate":
        ids = {c.id.lower() for c in question.candidates}
        concepts = {c.concept.lower() for c in question.candidates}
        if value.lower() not in ids | concepts:
            return (
                f"{lever.id} must name a screened candidate id or concept of the run "
                f"{sorted(c.id for c in question.candidates)}, got '{value}'"
            )
    return None


DR_IDS = Literal[
    "DR-01",
    "DR-02",
    "DR-03",
    "DR-04",
    "DR-05",
    "DR-06",
    "DR-07",
    "DR-08",
    "DR-09",
    "DR-10",
    "DR-11",
    "DR-12",
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
    "DR-11": "Backup capacity (robustness)",
}
"""AG-07 Decision Records DR-01…DR-10, plus DR-11 (pre-L-2): install the TL-15 robust backup
nameplates or keep the base-year ones (OPT-ROBUST-BACKUP), decided after P8. These are the
records every run writes; DR-12 (:data:`REVIEW_DR_ID`) exists only in runs with the review loop."""

REVIEW_DR_ID = "DR-12"
REVIEW_DR_TITLE = "User review"
"""Review loop (``cli run --review``, 2026-11-03): after the P9 verdict the user may object to the
recommended design; the objection is mapped to one bounded what-if lever, the what-if is computed
by the tools and the user adopts the change (P6–P9 rerun) or keeps the design. Not in
:data:`DR_TITLES` because it is written only when the review loop is on."""


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


class DecisionCriterion(StrictModel):
    """A decisive criterion the workflow offers for a Decision Record (AG-07, golden rule 1).

    The workflow evaluates it on tool results, so the LLM never compares numbers:
    ``best_option_ids`` are the eligible options that win under it. A choice names one criterion
    and its option must be one of them (a choice may not contradict its own stated reason).
    """

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$", max_length=64)
    key: str = Field(
        description="Evidence key it rests on: a tool-result field (e.g. 'lcoh_per_mwh') or an "
        "assumption id (e.g. 'DB-31')."
    )
    source: str = Field(description="Tool or register the key comes from, e.g. 'economics'.")
    text: str = Field(
        max_length=200,
        description="No-number statement, e.g. 'lowest lcoh_per_mwh among eligible options'.",
    )
    best_option_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list, description="Tool-result IDs.")

    @field_validator("text")
    @classmethod
    def _no_quantities(cls, text: str) -> str:
        return reject_llm_quantities(text)

    @model_validator(mode="after")
    def _text_names_key(self) -> DecisionCriterion:
        if self.key not in self.text:
            raise ValueError(f"criterion {self.id}: text must name its key '{self.key}'")
        return self


class DecisionQuestion(StrictModel):
    """A bounded choice the workflow offers an engine (spec §4.1 'LLMs choose within options')."""

    dr_id: DR_IDS
    title: str
    question: str
    phase: Phase
    role: Role
    options: list[DecisionOption] = Field(min_length=1)
    criteria: list[DecisionCriterion] = Field(
        default_factory=list, description="Decisive criteria offered by the workflow."
    )
    evidence_keys: list[str] = Field(
        default_factory=list,
        description="Keys a rationale may cite: field names in the evidence tool results, the "
        "tools themselves and assumption ids.",
    )

    @model_validator(mode="after")
    def _check_options(self) -> DecisionQuestion:
        ids = [option.id for option in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate option ids in {self.dr_id}: {ids}")
        if not any(option.eligible for option in self.options):
            raise ValueError(f"{self.dr_id} has no eligible option")
        eligible = {option.id for option in self.options if option.eligible}
        criterion_ids = [criterion.id for criterion in self.criteria]
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError(f"duplicate criterion ids in {self.dr_id}: {criterion_ids}")
        allowed, references = self.citable(), self.references()
        for criterion in self.criteria:
            if not set(criterion.best_option_ids) <= eligible:
                raise ValueError(f"{criterion.id}: best options must be eligible options")
            cited = [criterion.key, *key_tokens(criterion.text, references)]
            unknown = [k for k in cited if k not in allowed]
            if unknown:
                raise ValueError(f"{criterion.id} cites keys outside evidence_keys: {unknown}")
        return self

    def references(self) -> set[str]:
        """Return the evidence ids of options and criteria (removed before keys are read)."""
        ids = {e for option in self.options for e in option.evidence_ids}
        return ids | {e for criterion in self.criteria for e in criterion.evidence_ids}

    def citable(self) -> set[str]:
        """Return what a rationale may cite: evidence keys, ids and words of workflow text."""
        allowed = set(self.evidence_keys) | self.references()
        for option in self.options:
            allowed.add(option.id)
            allowed.update(key_tokens(f"{option.label} {option.notes}"))
        allowed.update(criterion.id for criterion in self.criteria)
        return allowed

    def criterion(self, criterion_id: str | None) -> DecisionCriterion | None:
        """Return the offered criterion with this id (None if not offered)."""
        return next((c for c in self.criteria if c.id == criterion_id), None)


class RejectedOption(StrictModel):
    """Why an option was not chosen."""

    option_id: str
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def _no_quantities(cls, text: str) -> str:
        return reject_llm_quantities(text)


class OptionChoice(StrictModel):
    """Structured output of an engine for a Decision Record question.

    ``criterion`` comes before ``option_id`` so a constrained decoder states the reason first.
    """

    dr_id: DR_IDS
    criterion: str | None = Field(
        default=None,
        max_length=64,
        description="id of the decisive criterion in question.criteria (required when offered).",
    )
    option_id: str
    rationale: str = Field(
        min_length=1,
        max_length=600,
        description="≤ 60 words, no numbers; names the criterion's key (e.g. 'lowest "
        "lcoh_per_mwh among guard-passed options in economics').",
    )
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
    criterion: DecisionCriterion | None = Field(
        default=None, description="The decisive criterion the choice rests on."
    )
    rationale: str
    rejected: list[RejectedOption]
    decision_source: DecisionSource
    stub_evidence: bool = Field(
        default=False, description="True if any evidence came from a stub tool (not final)."
    )
