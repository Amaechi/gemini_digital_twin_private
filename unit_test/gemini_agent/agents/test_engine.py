"""Unit tests for gemini_agent.agents.engine (AG-03, AG-04, AG-12, §5.4) — offline only.

LLM paths use Pydantic AI ``TestModel`` / ``FunctionModel``; no network call is made.
"""

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.test import TestModel

from gemini_agent.agents.engine import (
    EngineBudgetExceeded,
    EngineFailure,
    LLMEngine,
    PolicyEngine,
    ResilientEngine,
    build_ollama_model,
    create_decision_engine,
)
from gemini_agent.agents.roles import ROLES
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.runner.toolbox import default_toolbox
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import (
    Decision,
    DecisionContext,
    DecisionOption,
    DecisionQuestion,
    NextStep,
    OptionChoice,
    Phase,
    Role,
)
from unit_test.gemini_agent.llm_fakes import Counting, fake_model, policy_like

LLM_CONFIG = load_config("llm")
TOOLBOX = default_toolbox()


def _context(phase=Phase.P2, role=Role.SUBSURFACE, tokens=60_000, **kwargs) -> DecisionContext:
    return DecisionContext(
        run_id="t",
        phase=phase,
        phase_title="test",
        role=role,
        objective="test objective",
        user_prompt="design the geothermal plant",
        allowed_tools=[TOOLBOX.brief(tool) for tool in ROLES[role].tools],
        budget_left={"steps": 10, "tool_calls": 10, "tokens": tokens},
        **kwargs,
    )


def _question() -> DecisionQuestion:
    return DecisionQuestion(
        dr_id="DR-05",
        title="Storage yes/no",
        question="Storage?",
        phase=Phase.P5,
        role=Role.SYSTEM_ARCHITECT,
        options=[
            DecisionOption(id="no-storage", label="No storage"),
            DecisionOption(id="ates", label="HT-ATES", recommended=True),
            DecisionOption(id="tank", label="Tank", eligible=False, notes="not offered"),
        ],
    )


def _llm(func=policy_like, tmp_path=None) -> tuple[LLMEngine, LLMLogger | None]:
    logger = LLMLogger(tmp_path) if tmp_path is not None else None
    engine = LLMEngine(
        LLM_CONFIG,
        model=fake_model(func),
        logger=logger,
        run_id="t",
        validate_args=TOOLBOX.validate_args,
    )
    return engine, logger


# --------------------------------------------------------------------------- policy
def test_policy_decide_covers_refuse_tools_gaps_finalise():
    """P0 off-topic → refuse; required tool first; then open gaps; then finalise."""
    policy = PolicyEngine()
    refuse = policy.decide(_context(Phase.P0, Role.LEAD_ENGINEER, in_scope=False)).value
    assert refuse.steps[0].kind == "refuse"

    ctx = _context(
        required_tools_remaining=["reservoir_conditions"],
        default_args={"reservoir_conditions": {"depth_m": None}},
    )
    step = policy.decide(ctx).value.steps[0]
    assert (step.kind, step.tool, step.args) == (
        "call_tool",
        "reservoir_conditions",
        {"depth_m": None},
    )

    ctx = _context(
        Phase.P3, Role.SYSTEM_ARCHITECT, required_tools_remaining=["generate_candidates"]
    )
    assert policy.decide(ctx).value.steps[0].kind == "propose_candidates"

    ctx = _context(Phase.P1, Role.REQUIREMENTS, open_gaps=["DB-31"])
    step = policy.decide(ctx).value.steps[0]
    assert (step.kind, step.args) == ("request_input", {"assumption_id": "DB-31"})

    result = policy.decide(_context())
    assert result.value.steps[0].kind == "finalise" and result.source == "policy"


def test_policy_choose_prefers_recommended_eligible_option():
    """Recommended eligible option wins; every other option gets a rejection reason."""
    result = PolicyEngine().choose(_question(), _context())
    assert result.value.option_id == "ates" and result.source == "policy"
    reasons = {r.option_id: r.reason for r in result.value.rejected}
    assert set(reasons) == {"no-storage", "tank"} and reasons["tank"].startswith("Not eligible")


# --------------------------------------------------------------------------- LLM
def test_llm_engine_with_test_model_logs_the_call(tmp_path):
    """The TestModel output is validated (NativeOutput) and the call is logged with tokens."""
    decision = Decision(
        phase=Phase.P2,
        steps=[NextStep(kind="call_tool", tool="productivity_index", rationale="needed next")],
    )
    model = TestModel(
        custom_output_text=decision.model_dump_json(),
        profile={"supports_json_schema_output": True},
    )
    logger = LLMLogger(tmp_path)
    engine = LLMEngine(LLM_CONFIG, model=model, logger=logger, run_id="t")
    result = engine.decide(_context())
    assert result.source == "llm" and result.value == decision and result.retries == 0
    assert result.input_tokens > 0
    (record,) = logger.records()
    assert (record.agent, record.phase, record.decision_source) == ("subsurface", "P2", "llm")
    assert record.response["steps"][0]["tool"] == "productivity_index"
    assert record.messages and record.input_tokens == result.input_tokens


def test_llm_uses_native_output_mode(tmp_path):
    """Structured output is grammar-constrained (NativeOutput), not a tool or prompt."""
    seen = []

    def func(messages, info):
        seen.append(info.model_request_parameters.output_mode)
        return policy_like(messages, info)

    engine, _ = _llm(func)
    engine.decide(_context(required_tools_remaining=["thermal_breakthrough"]))
    assert seen == ["native"]


def test_invalid_output_retries_twice_then_policy_fallback(tmp_path):
    """Tool outside the role list → ModelRetry ×2 → EngineFailure → PolicyEngine (AG-03)."""
    bad = Decision(
        phase=Phase.P2, steps=[NextStep(kind="call_tool", tool="economics", rationale="x")]
    ).model_dump_json()
    counter = Counting(lambda messages, info: ModelResponse(parts=[TextPart(bad)]))
    engine, logger = _llm(counter, tmp_path)
    with pytest.raises(EngineFailure):
        engine.decide(_context())
    assert counter.calls == 1 + LLM_CONFIG.max_output_retries == 3

    resilient = ResilientEngine(engine)
    result = resilient.decide(_context(required_tools_remaining=["climate"]))
    assert result.source == "policy_fallback" and result.error
    assert result.value.steps[0].tool == "climate"
    records = logger.records()
    assert [r.decision_source for r in records] == ["policy_fallback", "policy_fallback"]
    assert records[0].retries == 2 and records[0].requests == 3 and records[0].error


def test_one_retry_then_valid_output(tmp_path):
    """A validation error is sent back; a corrected answer is accepted as source llm."""
    state = {"calls": 0}

    def func(messages, info):
        state["calls"] += 1
        if state["calls"] == 1:
            return ModelResponse(parts=[TextPart('{"phase": "P2", "steps": []}')])
        return policy_like(messages, info)

    engine, logger = _llm(func, tmp_path)
    result = engine.decide(_context(required_tools_remaining=["pressure_interference"]))
    assert result.source == "llm" and result.retries == 1
    assert result.value.steps[0].tool == "pressure_interference"
    assert logger.records()[0].retries == 1


def test_bad_arguments_and_unknown_gap_are_retried(tmp_path):
    """Arguments are validated against the tool schema; request_input needs an open gap."""
    bad_args = Decision(
        phase=Phase.P2,
        steps=[
            NextStep(
                kind="call_tool", tool="reservoir_conditions", args={"depth": 3}, rationale="x"
            )
        ],
    ).model_dump_json()
    engine, _ = _llm(lambda m, i: ModelResponse(parts=[TextPart(bad_args)]))
    with pytest.raises(EngineFailure, match="reservoir_conditions"):
        engine.decide(_context())

    bad_gap = Decision(
        phase=Phase.P1,
        steps=[
            NextStep(
                kind="request_input",
                tool="request_missing_input",
                args={"assumption_id": "DB-99"},
                rationale="x",
            )
        ],
    ).model_dump_json()
    engine, _ = _llm(lambda m, i: ModelResponse(parts=[TextPart(bad_gap)]))
    with pytest.raises(EngineFailure, match="OPEN_GAPS"):
        engine.decide(_context(Phase.P1, Role.REQUIREMENTS, open_gaps=["DB-31"]))


def test_token_budget_exceeded_is_not_masked_by_fallback(tmp_path):
    """A UsageLimits overrun raises EngineBudgetExceeded, also via ResilientEngine (AG-04)."""
    engine, logger = _llm(policy_like, tmp_path)
    with pytest.raises(EngineBudgetExceeded):
        ResilientEngine(engine).decide(_context(tokens=5))
    assert logger.records()[0].decision_source == "escalated"


def test_choice_validation_and_fallback(tmp_path):
    """An ineligible option is rejected; after the retries the policy chooses."""
    bad = OptionChoice(dr_id="DR-05", option_id="tank", rationale="x").model_dump_json()
    engine, _ = _llm(lambda m, i: ModelResponse(parts=[TextPart(bad)]), tmp_path)
    result = ResilientEngine(engine).choose(_question(), _context())
    assert result.source == "policy_fallback" and result.value.option_id == "ates"

    engine, logger = _llm(policy_like, tmp_path)
    result = engine.choose(_question(), _context())
    assert result.source == "llm" and result.value.option_id == "ates"
    assert logger.records()[-1].agent == "system_architect"


def test_ollama_model_is_built_from_config_without_network():
    """OpenAI-compatible Ollama model from config/llm.yaml; construction makes no request."""
    profile = LLM_CONFIG.active()
    assert build_ollama_model(LLM_CONFIG).model_name == profile.orchestrator
    assert build_ollama_model(LLM_CONFIG, "coder").model_name == profile.coder
    assert build_ollama_model(LLM_CONFIG, tag="qwen3.6:27b").model_name == "qwen3.6:27b"


def test_engine_factory():
    """Engine factory: policy → PolicyEngine; llm → LLM + policy fallback; else error."""
    assert isinstance(create_decision_engine("policy", LLM_CONFIG), PolicyEngine)
    engine = create_decision_engine("llm", LLM_CONFIG, model=fake_model())
    assert isinstance(engine, ResilientEngine) and isinstance(engine.fallback, PolicyEngine)
    with pytest.raises(ValueError):
        create_decision_engine("adk", LLM_CONFIG)  # type: ignore[arg-type]
