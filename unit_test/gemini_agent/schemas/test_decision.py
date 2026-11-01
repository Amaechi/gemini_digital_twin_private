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
