"""Unit tests for gemini_agent.schemas.decision (spec §5.3, AG-07)."""

import pytest
from pydantic import ValidationError

from gemini_agent.schemas.decision import (
    DR_TITLES,
    PRE_VALIDATE_HOOK,
    Decision,
    DecisionCriterion,
    DecisionOption,
    DecisionQuestion,
    NextStep,
    OptionChoice,
    Phase,
    Role,
    key_tokens,
    names_key,
)


def test_phase_order_and_dr_titles():
    """Eleven phases P0–P10 in order; Decision Records DR-01…DR-10 plus DR-11 (pre-L-2)."""
    assert [p.value for p in Phase] == [f"P{i}" for i in range(11)]
    assert sorted(DR_TITLES) == [f"DR-{i:02d}" for i in range(1, 12)]


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


def test_decision_pre_validate_hook_runs_only_with_a_context():
    """Fix 1: the LLM engine repairs raw data through the validation context; nobody else does."""
    raw = {
        "phase": "P3",
        "steps": [{"kind": "call_tool", "tool": "design_guard", "rationale": "x"}],
    }
    with pytest.raises(ValidationError, match="its own step kind"):
        Decision.model_validate(raw)

    def fix(data):
        return {**data, "steps": [{**step, "kind": "verify"} for step in data["steps"]]}

    decision = Decision.model_validate(raw, context={PRE_VALIDATE_HOOK: fix})
    assert decision.steps[0].kind == "verify"


def _criterion(**kwargs) -> DecisionCriterion:
    values = {
        "id": "lowest-lcoh_per_mwh",
        "key": "lcoh_per_mwh",
        "source": "economics",
        "text": "lowest lcoh_per_mwh among the eligible options",
        "best_option_ids": ["a"],
    }
    return DecisionCriterion(**{**values, **kwargs})


def test_decision_criterion_names_its_key_without_numbers():
    """Fix 4: a criterion's text names its key and states no number."""
    _criterion()
    with pytest.raises(ValidationError, match="must name its key"):
        _criterion(text="cheapest heat")
    with pytest.raises(ValidationError, match="quantities"):
        _criterion(text="lcoh_per_mwh below 50 USD")


def test_decision_question_checks_criteria():
    """Fix 4: best options must be eligible; a criterion cites only the question's evidence keys."""
    options = [DecisionOption(id="a", label="A"), DecisionOption(id="b", label="B", eligible=False)]
    kwargs = {"dr_id": "DR-01", "title": "t", "question": "q", "phase": "P5", "role": Role.REVIEWER}
    keys = ["lcoh_per_mwh", "economics"]
    question = DecisionQuestion(
        options=options, criteria=[_criterion()], evidence_keys=keys, **kwargs
    )
    assert question.criterion("lowest-lcoh_per_mwh").key == "lcoh_per_mwh"
    assert question.criterion("other") is None
    with pytest.raises(ValidationError, match="must be eligible"):
        DecisionQuestion(
            options=options,
            criteria=[_criterion(best_option_ids=["b"])],
            evidence_keys=keys,
            **kwargs,
        )
    with pytest.raises(ValidationError, match="outside evidence_keys"):
        DecisionQuestion(
            options=options, criteria=[_criterion()], evidence_keys=["economics"], **kwargs
        )
    with pytest.raises(ValidationError, match="duplicate criterion"):
        DecisionQuestion(options=options, criteria=[_criterion()] * 2, evidence_keys=keys, **kwargs)


def test_key_tokens_find_snake_case_citations_only():
    """Snake_case identifiers are cited keys; option ids, evidence refs and words are not."""
    text = (
        "lowest lcoh_per_mwh in economics via design_guard; 3-doublets wins; "
        "see config:design_basis.yaml#DB-01 and TR-012, not economics.fake_key"
    )
    refs = ["config:design_basis.yaml#DB-01", "TR-012"]
    assert key_tokens(text, refs) == ["lcoh_per_mwh", "design_guard", "fake_key"]
    assert "design_basis" in key_tokens(text)  # unless the reference is removed first


def test_names_key_matches_whole_words_only():
    """A criterion key must be named as a word: 'passed' is not in 'bypassed'."""
    assert names_key("candidates passed the guard", "passed")
    assert not names_key("the guard was bypassed", "passed")
    assert not names_key("lowest lcoh_per_mwh_total", "lcoh_per_mwh")
    assert names_key("register default (DB-31)", "DB-31")
