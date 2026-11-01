"""Unit tests for gemini_agent.schemas.decision (spec §5.3, AG-07)."""

import pytest
from pydantic import ValidationError

from gemini_agent.schemas.decision import (
    DR_TITLES,
    Decision,
    DecisionOption,
    DecisionQuestion,
    NextStep,
    OptionChoice,
    Phase,
    Role,
)


def test_phase_order_and_dr_titles():
    """Eleven phases P0–P10 in order; ten Decision Records DR-01…DR-10."""
    assert [p.value for p in Phase] == [f"P{i}" for i in range(11)]
    assert sorted(DR_TITLES) == [f"DR-{i:02d}" for i in range(1, 11)]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "call_tool"},  # call_tool needs a tool
        {"kind": "call_tool", "tool": "verify_design"},  # reserved for kind 'verify'
        {"kind": "request_input", "tool": "knowledge_search"},
        {"kind": "finalise", "tool": "knowledge_search"},
        {"kind": "verify", "tool": "simulate_annual"},
        {"kind": "call_tool", "tool": "climate", "args": {"nested": {"a": 1}}},  # flat args only
    ],
)
def test_next_step_kind_tool_rules(kwargs):
    """Kind ↔ tool validator (Plant Debugger ``validate_kind_tool``)."""
    with pytest.raises(ValidationError):
        NextStep(rationale="because", **kwargs)


def test_next_step_valid_and_rationale_limit():
    """Valid steps pass; a rationale over 60 words fails."""
    NextStep(kind="verify", tool="verify_design", args={"candidate_id": "C2-2d"}, rationale="ok")
    NextStep(kind="finalise", rationale="done")
    with pytest.raises(ValidationError):
        NextStep(kind="finalise", rationale="word " * 61)


def test_decision_step_count_and_strict_schema():
    """1–3 steps; the JSON schema forbids extra keys (grammar-constrained output)."""
    step = NextStep(kind="finalise", rationale="done")
    with pytest.raises(ValidationError):
        Decision(phase=Phase.P1, steps=[])
    with pytest.raises(ValidationError):
        Decision(phase=Phase.P1, steps=[step] * 4)
    assert Decision.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError):
        Decision.model_validate({"phase": "P1", "steps": [step.model_dump()], "extra": 1})


def test_decision_question_checks_options():
    """Duplicate option ids or no eligible option are rejected."""
    option = DecisionOption(id="a", label="A")
    kwargs = {"dr_id": "DR-01", "title": "t", "question": "q", "phase": "P5", "role": Role.REVIEWER}
    DecisionQuestion(options=[option], **kwargs)
    with pytest.raises(ValidationError):
        DecisionQuestion(options=[option, option], **kwargs)
    with pytest.raises(ValidationError):
        DecisionQuestion(options=[DecisionOption(id="a", label="A", eligible=False)], **kwargs)


def test_option_choice_rationale_limit():
    """The OptionChoice rationale is limited to 60 words."""
    OptionChoice(dr_id="DR-01", option_id="a", rationale="short")
    with pytest.raises(ValidationError):
        OptionChoice(dr_id="DR-01", option_id="a", rationale="x " * 61)


@pytest.mark.parametrize(
    "text",
    [
        "LCOH 45 EUR/MWh and -5 degC margin; 3 doublets at 5 MWth beat the rest",
        "LCOH is 45 USD/MWh",
        "emits 0.2 kg/kWh",
        "flow 300 m3/s",
        "1.5 MW_th of heat",
        "cost €12/MWh",
        "about $2 million",
        "COP of 4.2",
        "LCOH 41.2 is lowest",
        "LCOH 45 is lowest",
        "saves 20 percent",
        "a 1.2e3 kW chiller",
        "€5M cheaper",
        "returns at 55°C",
        "a 2500m deep well",
        "12kW",
        "1,200 kW",
        "5-10 years",
        "LCOH ４５ is lowest",  # full-width digits
        # numbers glued to any suffix
        "supply at 120C",
        "permeability 50mD",
        "flow 300m3h",
        "10x cheaper",
        "a 35d outage",
        "90pct availability",
        "12-15 doublets",
    ],
)
def test_llm_text_must_not_state_numbers(text):
    """AG-02 / golden rule 1: numbers (with or without units) in LLM text are refused."""
    with pytest.raises(ValidationError, match="quantities"):
        OptionChoice(dr_id="DR-01", option_id="a", rationale=text)
    with pytest.raises(ValidationError, match="quantities"):
        OptionChoice(
            dr_id="DR-01",
            option_id="a",
            rationale="ok",
            rejected=[{"option_id": "b", "reason": text}],
        )


@pytest.mark.parametrize(
    "text",
    [
        "Recommended by the deterministic tool ranking or the register default.",
        "Not eligible: fails the §10.1 materials check for saline brine",
        "Not eligible: outages would overlap; DB-30 requires non-overlapping outages",
        "depth-2500m wins on TR-003 and TR-007; see DR-01, C1-3d and P10.",
        "3-doublets has the lowest LCOH in TR-012 (RC-10, spec §5.4.3).",
        "dh-return-55 is the register default (DB-31); C0–C7 compared.",
        "Not eligible: candidates: none; excluded by DesignGuard/verifier: C1-2d, C1-3d",
        "Not eligible: verifier verdict: REJECTED",
        "dh-return-45.5 per REQ-01-DB-30, TR-012/TR-013; CO₂ and CO2 in m³.",
    ],
)
def test_ids_with_digits_are_allowed_in_llm_text(text):
    """Option, evidence, DR, assumption, candidate and phase ids may contain digits."""
    OptionChoice(dr_id="DR-01", option_id="a", rationale=text)


def test_escalation_reasons_must_not_state_numbers():
    """An escalate rationale becomes the run summary's escalation reason (AG-02)."""
    with pytest.raises(ValidationError, match="quantities"):
        NextStep(kind="escalate", rationale="Plant needs 4 doublets and LCOH 99 EUR/MWh")
    NextStep(kind="escalate", rationale="No candidate passes DesignGuard; see TR-004.")
    # tool-step rationales stay in the trace only; they may quote tool numbers
    NextStep(kind="call_tool", tool="knowledge_search", rationale="Check the 40 K limit.")
