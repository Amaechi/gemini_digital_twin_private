"""Unit tests for gemini_agent.agents.engine (AG-03, AG-04, AG-12, §5.4) — offline only.

LLM paths use Pydantic AI ``TestModel`` / ``FunctionModel``; no network call is made.
"""

import asyncio
import json
import tempfile
import time
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

from gemini_agent.agents.engine import (
    CHARS_PER_TOKEN,
    REQUEST_OVERHEAD_TOKENS,
    EngineBudgetExceeded,
    EngineFailure,
    LLMEngine,
    PolicyEngine,
    ResilientEngine,
    build_ollama_model,
    check_choice,
    choice_deps,
    close_model_clients,
    coerce_step_kinds,
    create_decision_engine,
)
from gemini_agent.agents.roles import ROLES
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.prompts.system import (
    FEW_SHOT,
    NO_TOOL_KIND_NOTES,
    choice_prompt,
    system_prompt,
)
from gemini_agent.runner.toolbox import default_toolbox
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import (
    NO_TOOL_KINDS,
    Decision,
    DecisionContext,
    DecisionCriterion,
    DecisionOption,
    DecisionQuestion,
    NextStep,
    OptionChoice,
    Phase,
    RejectedOption,
    Role,
)
from unit_test.gemini_agent.llm_fakes import Counting, fake_model, policy_like

pytestmark = pytest.mark.usefixtures("stub_tools")

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


def _llm(func, tmp_path) -> tuple[LLMEngine, LLMLogger]:
    logger = LLMLogger(tmp_path)
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

    engine, _ = _llm(func, tmp_path)
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
    engine, _ = _llm(lambda m, i: ModelResponse(parts=[TextPart(bad_args)]), tmp_path)
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
    engine, _ = _llm(lambda m, i: ModelResponse(parts=[TextPart(bad_gap)]), tmp_path)
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


def test_engine_factory(tmp_path):
    """Engine factory: policy → PolicyEngine; llm → LLM + policy fallback; else error."""
    assert isinstance(create_decision_engine("policy", LLM_CONFIG), PolicyEngine)
    engine = create_decision_engine(
        "llm", LLM_CONFIG, model=fake_model(), logger=LLMLogger(tmp_path)
    )
    assert isinstance(engine, ResilientEngine) and isinstance(engine.fallback, PolicyEngine)
    with pytest.raises(ValueError):
        create_decision_engine("adk", LLM_CONFIG)  # type: ignore[arg-type]


def test_llm_engine_requires_a_logger():
    """MEM-04: an LLM engine without a logger cannot be built, so no call goes unlogged."""
    with pytest.raises(ValueError, match="LLMLogger"):
        create_decision_engine("llm", LLM_CONFIG, model=fake_model())
    with pytest.raises(ValueError, match="LLMLogger"):
        LLMEngine(LLM_CONFIG, model=fake_model())


def test_zero_tokens_left_escalates_without_calling(tmp_path):
    """No LLM call is made once the phase has no tokens left (AG-04)."""
    counter = Counting(policy_like)
    engine, _ = _llm(counter, tmp_path)
    with pytest.raises(EngineBudgetExceeded, match="no tokens left"):
        engine.decide(_context(tokens=0))
    assert counter.calls == 0


def test_llm_cannot_refuse_an_in_scope_request(tmp_path):
    """The deterministic scope check is authoritative: refusing an in-scope request is retried."""
    refuse = Decision(
        phase=Phase.P0, steps=[NextStep(kind="refuse", rationale="looks off-topic")]
    ).model_dump_json()
    engine, _ = _llm(lambda m, i: ModelResponse(parts=[TextPart(refuse)]), tmp_path)
    with pytest.raises(EngineFailure, match="in scope"):
        engine.decide(_context(Phase.P0, Role.LEAD_ENGINEER, in_scope=True))
    result = engine.decide(_context(Phase.P0, Role.LEAD_ENGINEER, in_scope=False))
    assert result.value.steps[0].kind == "refuse"


def test_llm_settings_carry_ollama_context(tmp_path):
    """num_ctx and keep_alive from config/llm.yaml are sent with every request (best effort)."""
    seen = []

    def func(messages, info):
        seen.append(info.model_settings)
        return policy_like(messages, info)

    engine, _ = _llm(func, tmp_path)
    engine.decide(_context())
    body = seen[0]["extra_body"]
    assert body["options"]["num_ctx"] == LLM_CONFIG.ollama.num_ctx
    assert body["keep_alive"] == LLM_CONFIG.ollama.keep_alive


@pytest.mark.parametrize(
    "rationale",
    [
        "ATES saves 12 % and stores 40 MWh",
        "LCOH 45 EUR/MWh and -5 degC margin; 3 doublets at 5 MWth beat the rest",
    ],
)
def test_dr_rationale_must_not_state_quantities(tmp_path, rationale):
    """AG-02: an LLM rationale quoting a quantity is retried, then the policy decides."""
    bad = OptionChoice(dr_id="DR-05", option_id="ates", rationale="ok").model_dump()
    bad["rationale"] = rationale
    payload = json.dumps(bad)
    engine, _ = _llm(lambda m, i: ModelResponse(parts=[TextPart(payload)]), tmp_path)
    result = ResilientEngine(engine).choose(_question(), _context())
    assert result.source == "policy_fallback"
    with pytest.raises(ValueError, match="quantities"):
        OptionChoice(dr_id="DR-05", option_id="ates", rationale="return 55 degC is best")


# --------------------------------------------------------------------------- L-1 follow-up
def test_coerce_step_kinds_repairs_kinds_from_tool_names():
    """Fix 1: the tool name decides the kind; kinds without a tool keep their meaning."""
    log = [{"stale": "entry"}]
    raw = {
        "phase": "P3",
        "steps": [
            {"kind": "generate_model", "tool": "generate_candidates"},
            {"kind": "call_tool", "tool": "design_guard"},
            {"tool": "optimise_design"},  # kind missing
            {"kind": "tool_call", "tool": "request_missing_input"},  # invented kind
            {"kind": "verify", "tool": "verify_design"},  # already right
            {"kind": "finalise", "tool": "design_guard"},  # ambiguous: left for validation
        ],
    }
    fixed = coerce_step_kinds(raw, log)
    assert [step["kind"] for step in fixed["steps"]] == [
        "propose_candidates",
        "verify",
        "run_optimisation",
        "request_input",
        "verify",
        "finalise",
    ]
    assert [(c["tool"], c["from_kind"], c["to_kind"]) for c in log] == [
        ("generate_candidates", "generate_model", "propose_candidates"),
        ("design_guard", "call_tool", "verify"),
        ("optimise_design", "None", "run_optimisation"),
        ("request_missing_input", "tool_call", "request_input"),
    ]
    assert raw["steps"][0]["kind"] == "generate_model"  # input not mutated
    assert coerce_step_kinds({"phase": "P3", "steps": [{"kind": "finalise"}]}, log) and log == []


def test_llm_kind_errors_are_coerced_not_retried(tmp_path):
    """Fix 1: L-1 kind errors (reserved tools as call_tool, invented kinds) validate first try."""
    payload = json.dumps(
        {
            "phase": "P3",
            "steps": [
                {"kind": "generate_model", "tool": "generate_candidates", "rationale": "needed"},
                {"kind": "call_tool", "tool": "design_guard", "args": {}, "rationale": "limits"},
            ],
        }
    )
    counter = Counting(lambda m, i: ModelResponse(parts=[TextPart(payload)]))
    engine, logger = _llm(counter, tmp_path)
    result = engine.decide(_context(Phase.P3, Role.SYSTEM_ARCHITECT))
    assert result.source == "llm" and counter.calls == 1 and result.retries == 0
    assert [(s.kind, s.tool) for s in result.value.steps] == [
        ("propose_candidates", "generate_candidates"),
        ("verify", "design_guard"),
    ]
    assert [c["to_kind"] for c in result.kind_coercions] == ["propose_candidates", "verify"]
    assert logger.records()[0].decision_source == "llm"

    # A tool on a kind that takes none is still an error (retried, then the policy decides).
    bad = json.dumps(
        {"phase": "P3", "steps": [{"kind": "finalise", "tool": "design_guard", "rationale": "x"}]}
    )
    engine, _ = _llm(lambda m, i: ModelResponse(parts=[TextPart(bad)]), tmp_path)
    with pytest.raises(EngineFailure, match="take no tool"):
        engine.decide(_context(Phase.P3, Role.SYSTEM_ARCHITECT))


def test_system_prompt_shows_the_kind_map_and_two_valid_examples():
    """Fix 1: STEP_KINDS lists each role tool's kind; both few-shot examples validate."""
    architect = system_prompt(Role.SYSTEM_ARCHITECT)
    for pair in (
        "generate_candidates=propose_candidates",
        "design_guard=verify",
        "optimise_design=run_optimisation",
        "simulate_annual=call_tool",
    ):
        assert pair in architect
    assert "request_missing_input=request_input" in system_prompt(Role.REQUIREMENTS)
    assert "verify_design=verify" in system_prompt(Role.REVIEWER)
    examples = [line.split(": ", 1)[1] for line in FEW_SHOT.splitlines()]
    decisions = [Decision.model_validate_json(example) for example in examples]
    kinds = {step.kind for decision in decisions for step in decision.steps}
    assert len(examples) == 2 and kinds - {"call_tool"}


def test_llm_settings_carry_the_call_limits(tmp_path):
    """Fix 2: thinking off, max output tokens and a request deadline on every call."""
    seen = []

    def func(messages, info):
        seen.append(info.model_settings)
        return policy_like(messages, info)

    engine, _ = _llm(func, tmp_path)
    engine.decide(_context())
    calls = LLM_CONFIG.calls
    assert calls.thinking is False
    assert seen[0]["openai_reasoning_effort"] == "none"
    assert seen[0]["max_tokens"] == calls.max_output_tokens
    assert seen[0]["request_timeout"] == seen[0]["timeout"] == calls.request_timeout_s

    thinking = LLM_CONFIG.model_copy(update={"calls": calls.model_copy(update={"thinking": True})})
    engine = LLMEngine(thinking, model=fake_model(func), logger=LLMLogger(tmp_path))
    engine.decide(_context())
    assert "openai_reasoning_effort" not in seen[1]


def test_ollama_request_body_is_schema_constrained_and_capped():
    """Fix 2: the wire body Ollama /v1 reads (mock transport, no network).

    NativeOutput sends response_format json_schema; max_tokens (not max_completion_tokens, which
    Ollama ignores); reasoning_effort 'none' (Ollama think=false, so the grammar applies from the
    first token); keep_alive.
    """
    httpx2 = pytest.importorskip("httpx2")
    from openai import AsyncOpenAI

    bodies = []
    answer = Decision(
        phase=Phase.P2,
        steps=[NextStep(kind="call_tool", tool="productivity_index", rationale="needed next")],
    ).model_dump_json()

    def handler(request):
        bodies.append(json.loads(request.content))
        message = {"role": "assistant", "content": answer}
        return httpx2.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
            },
        )

    client = AsyncOpenAI(
        base_url=LLM_CONFIG.ollama.base_url,
        api_key="ollama",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    model = build_ollama_model(LLM_CONFIG, openai_client=client)
    # The conftest blocks model requests; this one only reaches the mock transport above.
    with tempfile.TemporaryDirectory() as tmp, override_allow_model_requests(True):
        engine = LLMEngine(LLM_CONFIG, model=model, logger=LLMLogger(Path(tmp)))
        assert engine.decide(_context()).value == Decision.model_validate_json(answer)
    (body,) = bodies
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "Decision"
    assert "steps" in body["response_format"]["json_schema"]["schema"]["properties"]
    assert body["max_tokens"] == LLM_CONFIG.calls.max_output_tokens
    assert "max_completion_tokens" not in body
    assert body["reasoning_effort"] == "none"
    assert body["keep_alive"] == LLM_CONFIG.ollama.keep_alive

    cloud = build_ollama_model(LLM_CONFIG, tag="qwen3.6:35b-cloud")
    assert cloud.profile["supports_json_schema_output"] is False


def test_hung_call_times_out_into_the_policy_fallback(tmp_path):
    """Fix 2: a call past request_timeout_s is cancelled and the policy decides (AG-03)."""

    async def hang(messages, info):
        await asyncio.sleep(5)
        return policy_like(messages, info)

    calls = LLM_CONFIG.calls.model_copy(update={"request_timeout_s": 0.05})
    config = LLM_CONFIG.model_copy(update={"calls": calls})
    logger = LLMLogger(tmp_path)
    engine = LLMEngine(config, model=fake_model(hang), logger=logger)
    start = time.perf_counter()
    result = ResilientEngine(engine).decide(_context(required_tools_remaining=["climate"]))
    assert time.perf_counter() - start < 2.0
    assert result.source == "policy_fallback" and "ModelRequestTimeout" in result.error
    assert logger.records()[0].decision_source == "policy_fallback"


def _criteria_question() -> DecisionQuestion:
    return DecisionQuestion(
        dr_id="DR-01",
        title="Number of doublets",
        question="How many doublets?",
        phase=Phase.P5,
        role=Role.SYSTEM_ARCHITECT,
        options=[
            DecisionOption(id="2-doublets", label="2", evidence_ids=["TR-007"], recommended=True),
            DecisionOption(id="3-doublets", label="3", evidence_ids=["TR-007"]),
        ],
        criteria=[
            DecisionCriterion(
                id="ranking-lowest-lcoh_per_mwh",
                key="lcoh_per_mwh",
                source="economics",
                text="lowest lcoh_per_mwh among guard-passed candidates in economics",
                best_option_ids=["2-doublets"],
                evidence_ids=["TR-007"],
            ),
            DecisionCriterion(
                id="lowest-co2_t_per_yr",
                key="co2_t_per_yr",
                source="economics",
                text="lowest co2_t_per_yr among the eligible options in economics",
                best_option_ids=["3-doublets"],
                evidence_ids=["TR-007"],
            ),
        ],
        evidence_keys=["co2_t_per_yr", "economics", "lcoh_per_mwh", "unmet_heating_mwh"],
    )


@pytest.mark.parametrize(
    ("choice", "error"),
    [
        ({"option_id": "2-doublets", "rationale": "it is recommended"}, "criterion must be"),
        (
            {"criterion": "lowest-co2_t_per_yr", "option_id": "2-doublets", "rationale": "co2"},
            "contradicts",
        ),
        (
            {
                "criterion": "ranking-lowest-lcoh_per_mwh",
                "option_id": "2-doublets",
                "rationale": "2-doublets is selected because it is recommended",
            },
            "evidence key 'lcoh_per_mwh'",
        ),
        (
            {
                "criterion": "ranking-lowest-lcoh_per_mwh",
                "option_id": "2-doublets",
                "rationale": "lowest lcoh_per_mwh_total in economics",
            },
            "evidence key 'lcoh_per_mwh'",
        ),
        (
            {
                "criterion": "ranking-lowest-lcoh_per_mwh",
                "option_id": "2-doublets",
                "rationale": "lowest lcoh_per_mwh and best npv_usd in economics",
            },
            "npv_usd",
        ),
        (
            {
                "criterion": "ranking-lowest-lcoh_per_mwh",
                "option_id": "2-doublets",
                "rationale": "lowest lcoh_per_mwh, see economics.fake_key",
            },
            "fake_key",
        ),
    ],
)
def test_choice_must_rest_on_an_offered_criterion(choice, error):
    """Fix 4: criterion offered, choice among its best options, key named, cited keys exist."""
    question = _criteria_question()
    message = check_choice(choice_deps(question), OptionChoice(dr_id="DR-01", **choice))
    assert message is not None and error in message
    good = OptionChoice(
        dr_id="DR-01",
        criterion="lowest-co2_t_per_yr",
        option_id="3-doublets",
        rationale="lowest co2_t_per_yr in economics; unmet_heating_mwh is equal for both.",
        rejected=[RejectedOption(option_id="2-doublets", reason="higher co2_t_per_yr")],
    )
    assert check_choice(choice_deps(question), good) is None


def test_hollow_llm_rationale_is_retried_then_grounded(tmp_path):
    """Fix 4: L-1's 'selected because it is recommended' is retried; the retry names the key."""
    hollow = json.dumps(
        {
            "dr_id": "DR-01",
            "criterion": "ranking-lowest-lcoh_per_mwh",
            "option_id": "2-doublets",
            "rationale": "2-doublets is selected because it is recommended",
        }
    )
    grounded = hollow.replace(
        "2-doublets is selected because it is recommended",
        "lowest lcoh_per_mwh among guard-passed candidates in economics",
    )
    answers = iter([hollow, grounded])
    engine, _ = _llm(lambda m, i: ModelResponse(parts=[TextPart(next(answers))]), tmp_path)
    result = engine.choose(_criteria_question(), _context())
    assert result.source == "llm" and result.retries == 1
    assert result.value.criterion == "ranking-lowest-lcoh_per_mwh"

    policy = PolicyEngine().choose(_criteria_question(), _context()).value
    assert policy.criterion == "ranking-lowest-lcoh_per_mwh" and policy.option_id == "2-doublets"
    assert "lcoh_per_mwh" in policy.rationale
    assert check_choice(choice_deps(_criteria_question()), policy) is None


def test_step_kinds_names_finalise_first_with_the_meaning_of_each_no_tool_kind():
    """L-1 P0 follow-up: finalise leads the no-tool kinds; delegate never ends a phase."""
    prompt = system_prompt(Role.LEAD_ENGINEER)
    block = prompt.split("Steps with tool null: ", 1)[1].split("\n", 1)[0]
    assert block.startswith("finalise (")
    assert "in-scope answer in P0" in block
    assert "delegate (note to the runner; never ends a phase)" in block
    assert "refuse (P0 only, off-topic request)" in block
    assert block.index("finalise") < block.index("delegate")
    assert set(NO_TOOL_KIND_NOTES) == NO_TOOL_KINDS  # a new no-tool kind cannot vanish silently


class _FakeAsyncClient:
    """Stands in for ``openai.AsyncOpenAI``: ``close`` is a coroutine that must run on a loop."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeModel:
    def __init__(self) -> None:
        self.client = _FakeAsyncClient()


def test_close_model_clients_closes_the_clients_and_the_event_loop():
    """WP-15: the clients are closed on the live loop, then the loop (no kqueue traceback)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    model, coder = _FakeModel(), _FakeModel()
    close_model_clients(model, None, coder, TestModel())  # None and client-less models are skipped
    assert model.client.closed and coder.client.closed
    assert loop.is_closed()
    with pytest.raises(RuntimeError):  # no current loop is left behind
        asyncio.get_event_loop()
    close_model_clients(model)  # idempotent: no loop → nothing to do, no error

    # a client whose close() raises never replaces the CLI exit code with a traceback
    class _Broken:
        async def close(self) -> None:
            raise OSError("socket already gone")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    broken = _FakeModel()
    broken.client = _Broken()
    close_model_clients(broken)
    assert loop.is_closed()

    # a running loop is never closed from inside
    async def _inside() -> None:
        inner = _FakeModel()
        close_model_clients(inner)
        assert not inner.client.closed and not asyncio.get_running_loop().is_closed()

    asyncio.run(_inside())


def test_engines_close_without_error_and_a_later_call_still_runs(tmp_path):
    """``ResilientEngine.close`` reaches both engines; a later call opens a fresh loop.

    A TestModel engine closes harmlessly (no client); pydantic-ai creates a new event loop for
    the next ``run_sync``.
    """
    engine = create_decision_engine(
        "llm", LLM_CONFIG, model=fake_model(), logger=LLMLogger(tmp_path)
    )
    assert isinstance(engine, ResilientEngine)
    assert engine.decide(_context()).source == "llm"
    engine.close()
    PolicyEngine().close()  # the default close is a no-op
    assert engine.decide(_context()).source == "llm"  # new event loop, same engine
    engine.close()


# --------------------------------------------------------------------------- runner J: DR floor
def _with_usage(input_tokens: int, output_tokens: int = 0):
    """policy_like whose every reply reports a fixed usage (FunctionModel keeps a set usage)."""

    def func(messages, info):
        response = policy_like(messages, info)
        response.usage = RequestUsage(input_tokens=input_tokens, output_tokens=output_tokens)
        return response

    return func


def test_choice_call_is_floored_at_its_own_token_ceiling(tmp_path):
    """Runner J: a Decision Record choice runs with no tokens left in the phase; its usage limit
    is floored at the call's ceiling (retries included), so a call within ``calls`` completes."""
    engine, logger = _llm(policy_like, tmp_path)
    result = engine.choose(_question(), _context(tokens=0))
    assert result.source == "llm" and result.value.option_id == "ates"
    assert logger.records()[-1].decision_source == "llm"

    prompt = choice_prompt(_question(), _context())
    ceiling = engine.call_token_ceiling(prompt, Role.SYSTEM_ARCHITECT)
    n = LLM_CONFIG.max_output_retries + 2
    outputs = LLM_CONFIG.calls.max_output_tokens * n * (n + 1) // 2
    prompt_tokens = (len(prompt) + len(system_prompt(Role.SYSTEM_ARCHITECT))) // CHARS_PER_TOKEN
    assert ceiling == n * (prompt_tokens + 1 + REQUEST_OVERHEAD_TOKENS) + outputs
    # A call that uses more than the phase has left but less than its ceiling completes …
    engine, _ = _llm(_with_usage(ceiling - 100), tmp_path)
    assert engine.choose(_question(), _context(tokens=5)).source == "llm"
    # … while one past its own ceiling raises EngineBudgetExceeded through ResilientEngine too
    # (the runner then lets the policy choose the DR and records the overrun).
    engine, logger = _llm(_with_usage(ceiling + 1), tmp_path)
    with pytest.raises(EngineBudgetExceeded, match="total_tokens_limit"):
        ResilientEngine(engine).choose(_question(), _context(tokens=5))
    assert logger.records()[-1].decision_source == "escalated"


def test_decisions_keep_the_phase_budget_as_their_limit(tmp_path):
    """The floor is for Decision Records only: a decision at the phase's last tokens escalates."""
    engine, logger = _llm(_with_usage(2_000), tmp_path)
    assert engine.decide(_context(tokens=2_500)).source == "llm"
    with pytest.raises(EngineBudgetExceeded, match="total_tokens_limit"):
        engine.decide(_context(tokens=1_500))
    with pytest.raises(EngineBudgetExceeded, match="no tokens left"):
        engine.decide(_context(tokens=0))
    assert [r.decision_source for r in logger.records()] == ["llm", "escalated"]
