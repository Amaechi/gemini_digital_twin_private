"""Unit tests for gemini_agent.runner.design_runner (spec §5.2, AG-03…AG-12) — offline only."""

import json

import pytest
from pydantic_ai.messages import ModelResponse, TextPart

from gemini_agent.agents.engine import (
    EngineResult,
    LLMEngine,
    PolicyEngine,
    ResilientEngine,
)
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.runner.design_runner import DesignRunner, InvalidAnswer
from gemini_agent.runner.phases import PHASE_SPECS, PhaseBudget
from gemini_agent.runner.state import ToolRecord
from gemini_agent.runner.tool_stubs import VerificationOutput, stub_toolbox
from gemini_agent.runner.toolbox import CandidateArgs, OptimiseArgs, ToolSpec
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import StrictModel, ToolResult
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import (
    DR_TITLES,
    Decision,
    DesignVerdict,
    NextStep,
    Phase,
    Role,
    RunStatus,
)
from gemini_agent.schemas.results import KPISet
from unit_test.gemini_agent.llm_fakes import (
    Counting,
    always_invalid,
    fake_model,
    kinds_as_call_tool,
    output_name,
    policy_like,
    user_prompt,
)

pytestmark = pytest.mark.usefixtures("stub_tools")


def _runner(tmp_path, engine=None, **kwargs) -> DesignRunner:
    return DesignRunner(engine or PolicyEngine(), runs_root=tmp_path, **kwargs)


def _phase_starts(runner: DesignRunner, run_id: str) -> list[str]:
    events = runner.store(run_id).list_events(run_id)
    return [e.phase for e in events if e.type == "PHASE_START"]


def _verifier(verdict: DesignVerdict, stub: bool = False) -> ToolSpec:
    def func(args, context):
        candidate = context.selected_id if args.candidate_id == "all" else args.candidate_id
        return ToolResult[VerificationOutput](
            data=VerificationOutput(
                candidate_id=candidate,
                verdict=verdict,
                violations=["brine cooling 45 K > 40 K"] if verdict == "REJECTED" else [],
                verifier="fake",
            ),
            summary=f"fake verifier: {verdict}",
        )

    return ToolSpec("verify_design", "fake", CandidateArgs, func, owner="test", stub=stub)


def test_policy_run_completes_p0_to_p10_with_artefacts(tmp_path):
    """AG-12: the whole pipeline runs with the policy engine; outputs are written."""
    runner = _runner(tmp_path)
    result = runner.start("", run_id="r1")
    assert result.status == RunStatus.COMPLETED and result.phase == Phase.P10
    assert _phase_starts(runner, "r1") == [p.value for p in Phase]
    assert sorted(result.decision_records) == sorted(DR_TITLES)
    # Stub verifier: never ACCEPTED (golden rule 3); approval pending in assume mode.
    assert result.verdict == "UNVERIFIED" and result.selected_id == "C1-3d"
    run_dir = tmp_path / "r1"
    for name in ("run_summary.json", "events.jsonl", "token_summary.json", "report_draft.md"):
        assert (run_dir / name).exists(), name
    assert len(list((run_dir / "decision_records").glob("DR-*.json"))) == len(DR_TITLES)
    summary = json.loads((run_dir / "run_summary.json").read_text())
    assert summary["approval"] == "pending" and summary["stub_tool_calls"] > 0
    assert any(
        "DB-31 = 55.0 degC (assumed" in label for label in summary["assumptions_and_answers"]
    )
    state = runner.load_state("r1")
    assert all(record.decision_source == "policy" for record in state.decision_records)
    assert all(record.stub_evidence for record in state.decision_records if record.dr_id != "DR-08")
    with pytest.raises(ValueError, match="already exists"):
        runner.start("", run_id="r1")


def test_pause_and_resume_across_runner_instances(tmp_path):
    """AG-05: request_missing_input pauses (state in SQLite) and a new process resumes it."""
    first = _runner(tmp_path)
    result = first.start("design the geothermal plant", run_id="p1", input_mode="pause")
    assert result.status == RunStatus.PAUSED and result.phase == Phase.P1
    assert result.pending.assumption_id == "DB-30"
    first.close()

    second = _runner(tmp_path)  # simulates a new CLI process
    with pytest.raises(InvalidAnswer):
        second.resume("p1", "C")  # DB-30 must be A or B
    assert second.load_state("p1").status == RunStatus.PAUSED
    assert second.resume("p1", "A").pending.assumption_id == "DB-31"
    with pytest.raises(InvalidAnswer, match="below the 100 degC"):
        second.resume("p1", "120")
    with pytest.raises(InvalidAnswer, match="number"):
        second.resume("p1", "warm")
    assert second.resume("p1", "60").pending.assumption_id == "DB-32"
    result = second.resume("p1", "25")
    assert result.status == RunStatus.PAUSED and result.pending.request_id == "APPROVAL"
    result = second.resume("p1", "yes")
    assert result.status == RunStatus.COMPLETED
    with pytest.raises(ValueError, match="not paused"):
        second.resume("p1", "yes")

    state = second.load_state("p1")
    assert state.approval == "approved"
    assert [a.source for a in state.answers] == ["user"] * 4
    assert {c.heating_return_degc for c in state.candidates} == {60.0}
    dr08 = next(r for r in state.decision_records if r.dr_id == "DR-08")
    assert dr08.choice == "dh-return-60"
    durable = [e for e in second.store("p1").list_events("p1") if e.type == "USER_ANSWER"]
    assert len(durable) == 4 and all(e.payload["durable"] for e in durable)


def test_ask_mode_uses_the_terminal_callback(tmp_path):
    """Interactive mode asks; invalid answers are re-asked; blank keeps the default."""
    answers = iter(["B", "abc", "65", "", "no"])
    asked = []

    def ask(request):
        asked.append(request.request_id)
        return next(answers)

    result = _runner(tmp_path, ask_fn=ask).start("", run_id="a1", input_mode="ask")
    assert result.status == RunStatus.COMPLETED
    state = _runner(tmp_path).load_state("a1")
    labels = [a.label for a in state.answers]
    assert labels[1] == "DB-31 = 65.0 degC (user answer)"
    assert labels[2] == "DB-32 = 26.0 degC (assumed: register default)"
    assert state.approval == "rejected" and len(asked) == 5


def test_step_budget_exceeded_escalates(tmp_path):
    """AG-04: exceeding the step budget of a phase escalates with a summary."""
    budgets = {Phase.P2: PhaseBudget(max_steps=2)}
    result = _runner(tmp_path, budgets=budgets).start("", run_id="b1")
    assert result.status == RunStatus.ESCALATED and result.phase == Phase.P2
    assert "step budget exceeded in P2" in result.summary and "2 steps" in result.summary


def test_tool_call_budget_exceeded_escalates(tmp_path):
    """AG-04: the tool-call budget is enforced too."""
    budgets = {Phase.P3: PhaseBudget(max_tool_calls=1)}
    result = _runner(tmp_path, budgets=budgets).start("", run_id="b2")
    assert result.status == RunStatus.ESCALATED and "tool-call budget" in result.summary


def test_off_topic_prompt_is_refused(tmp_path):
    """AG-06: off-topic requests stop in P0 with a redirecting refusal."""
    runner = _runner(tmp_path)
    result = runner.start("What is the best pizza in Rome?", run_id="o1")
    assert result.status == RunStatus.REFUSED and result.phase == Phase.P0
    assert "outside that scope" in result.summary
    assert _phase_starts(runner, "o1") == ["P0"]


def test_constraint_conflicts_are_recorded(tmp_path):
    """AG-06: a request beyond the hard limits is explained and the run stays within limits."""
    runner = _runner(tmp_path)
    result = runner.start("Design the plant with 5 doublets", run_id="c1")
    assert result.status == RunStatus.COMPLETED
    state = runner.load_state("c1")
    assert state.conflicts and "1–3 doublets" in state.conflicts[0]
    assert max(c.doublets for c in state.candidates) <= 3


def test_rejected_verification_loops_three_times_then_escalates(tmp_path):
    """P7 FAIL ⇒ back to P6 (≤ 3 loops), then escalate; violations reach the context."""
    toolbox = stub_toolbox()
    toolbox.register(_verifier(DesignVerdict.REJECTED))
    runner = _runner(tmp_path, toolbox=toolbox)
    result = runner.start("", run_id="v1")
    assert result.status == RunStatus.ESCALATED and "after 3 loops" in result.summary
    state = runner.load_state("v1")
    assert state.loop_count == 4 and state.violations == ["brine cooling 45 K > 40 K"]
    starts = _phase_starts(runner, "v1")
    assert starts.count("P6") == 4 and starts.count("P8") == 0
    # each loop moves on: a REJECTED candidate is never selected again
    verified = [v.candidate_id for v in state.verifications]
    assert verified == ["C1-3d", "C2-2d", "C3-2d", "C4-2d"]
    assert set(
        json.loads((tmp_path / "v1" / "run_summary.json").read_text())["excluded_candidates"]
    ) == set(verified)


def test_guard_failed_candidates_are_never_selected(tmp_path):
    """Hard limits (rule 4): a candidate failing DesignGuard is not shortlisted or offered."""
    from gemini_agent.runner.tool_stubs import GuardCheck, GuardOutput

    def guard(args, context):
        checks = [
            GuardCheck(candidate_id=c.id, passed=c.concept != "C1", violations=["test"])
            for c in context.candidates
        ]
        return ToolResult[GuardOutput](data=GuardOutput(checks=checks), summary="fake guard")

    toolbox = stub_toolbox()
    toolbox.register(ToolSpec("design_guard", "fake", CandidateArgs, guard, owner="test"))
    runner = _runner(tmp_path, toolbox=toolbox)
    result = runner.start("", run_id="g1")
    assert result.status == RunStatus.COMPLETED and result.selected_id == "C2-2d"
    state = runner.load_state("g1")
    assert state.guard_failed == ["C1-3d"] and "C1-3d" not in state.shortlist
    dr03 = next(r for r in state.decision_records if r.dr_id == "DR-03")
    chp = next(o for o in dr03.options if o.id == "gas-boiler-chp-topup")
    assert not chp.eligible and dr03.choice == "hthp-series"

    # a later single-candidate guard result merges instead of replacing the list
    single = ToolResult[GuardOutput](
        data=GuardOutput(checks=[GuardCheck(candidate_id="C3-2d", passed=False)]), summary="x"
    )
    runner._apply_result(state, PHASE_SPECS[Phase.P3], "design_guard", "TR-999", single, {})
    assert state.guard_failed == ["C1-3d", "C3-2d"]


def test_p1_gaps_skipped_by_the_agent_still_follow_the_input_mode(tmp_path):
    """AG-05: if the LLM finalises P1 without asking, pause mode still asks the user."""

    def finalise_early(messages, info):
        if output_name(info) == "Decision":
            context = json.loads(user_prompt(messages).split("CONTEXT:\n", 1)[1])
            if context["phase"] == "P1" and not context["required_tools_remaining"]:
                step = NextStep(kind="finalise", rationale="done")
                body = Decision(phase=Phase.P1, steps=[step]).model_dump_json()
                return ModelResponse(parts=[TextPart(body)])
        return policy_like(messages, info)

    engine, _ = _llm_engine(tmp_path, "s1", finalise_early)
    result = _runner(tmp_path, engine=engine).start("", run_id="s1", input_mode="pause")
    assert result.status == RunStatus.PAUSED and result.pending.assumption_id == "DB-30"


def test_non_finite_answers_are_rejected(tmp_path):
    """'nan' / 'inf' are invalid numbers; the run stays paused."""
    runner = _runner(tmp_path)
    runner.start("", run_id="n1", input_mode="pause")
    runner.resume("n1", "A")
    for bad in ("nan", "-inf"):
        with pytest.raises(InvalidAnswer, match="finite"):
            runner.resume("n1", bad)
    state = runner.load_state("n1")
    assert state.status == RunStatus.PAUSED and "DB-31" in state.open_gaps


def test_reviewer_sees_tool_evidence_only(tmp_path):
    """AG-08: the reviewer's history holds tool results, not earlier agent decisions."""
    runner = _runner(tmp_path)
    runner.start("", run_id="rv", input_mode="assume")
    state = runner.load_state("rv")
    reviewer = runner._history(state, Role.REVIEWER)
    assert reviewer and {item["type"] for item in reviewer} <= {"TOOL_RESULT", "TOOL_ERROR"}
    assert "DECISION" in {item["type"] for item in runner._history(state)}


def test_history_switches_for_the_naive_token_profile(tmp_path):
    """MEM-06 (WP-13 S10): defaults keep the last compacted events; naive keeps everything."""
    runner = _runner(tmp_path)
    runner.start("", run_id="h1", input_mode="assume")
    state = runner.load_state("h1")
    optimised = runner._history(state)
    naive = _runner(tmp_path, history_events=None, compact_history=False)._history(state)
    assert len(optimised) == 12 < len(naive)
    decisions = [item for item in naive if item["type"] == "DECISION"]
    assert decisions and "steps" in decisions[0] and "phase" in decisions[0]


def test_only_a_real_verifier_can_accept(tmp_path):
    """AG-11: ACCEPTED comes from a real verifier on real evidence; otherwise it is downgraded."""
    import dataclasses

    toolbox = stub_toolbox()
    toolbox.register(_verifier(DesignVerdict.ACCEPTED))
    result = _runner(tmp_path, toolbox=toolbox).start("", run_id="stub_evidence")
    assert result.status == RunStatus.COMPLETED and result.verdict == "UNVERIFIED"

    for name in toolbox.names():
        toolbox.register(dataclasses.replace(toolbox.spec(name), stub=False))
    result = _runner(tmp_path, toolbox=toolbox).start("", run_id="ok")
    assert result.status == RunStatus.COMPLETED and result.verdict == "ACCEPTED"

    toolbox.register(_verifier(DesignVerdict.ACCEPTED, stub=True))
    result = _runner(tmp_path, toolbox=toolbox).start("", run_id="stub")
    assert result.verdict == "UNVERIFIED"


def test_real_verifier_unverified_escalates_only_without_stubs(tmp_path):
    """WP-09 decision: UNVERIFIED never loops back; it escalates at P9 once no tool is a stub."""
    import dataclasses

    toolbox = stub_toolbox()
    toolbox.register(_verifier(DesignVerdict.UNVERIFIED))
    result = _runner(tmp_path, toolbox=toolbox).start("", run_id="stubs")
    assert result.status == RunStatus.COMPLETED and result.verdict == "UNVERIFIED"

    for name in toolbox.names():
        toolbox.register(dataclasses.replace(toolbox.spec(name), stub=False))
    runner = _runner(tmp_path, toolbox=toolbox)
    result = runner.start("", run_id="real")
    assert result.status == RunStatus.ESCALATED and result.phase == Phase.P9
    assert "UNVERIFIED" in result.summary
    events = runner.store("real").list_events("real")
    assert not [e for e in events if e.type == "LOOP_BACK"]


def _llm_engine(tmp_path, run_id, func):
    logger = LLMLogger(tmp_path / run_id)
    llm = LLMEngine(
        load_config("llm"),
        model=fake_model(func),
        logger=logger,
        run_id=run_id,
        validate_args=stub_toolbox().validate_args,
    )
    return ResilientEngine(llm), logger


def test_llm_engine_path_completes_and_logs_every_call(tmp_path):
    """--engine llm with a FunctionModel: every decision and DR goes through the logger."""
    counter = Counting(policy_like)
    engine, logger = _llm_engine(tmp_path, "l1", counter)
    runner = _runner(tmp_path, engine=engine)
    result = runner.start("", run_id="l1")
    assert result.status == RunStatus.COMPLETED
    state = runner.load_state("l1")
    decisions = [e for e in runner.store("l1").list_events("l1") if e.type == "DECISION"]
    records = logger.records()
    assert len(records) == counter.calls == len(decisions) + len(state.decision_records)
    assert {r.decision_source for r in records} == {"llm"}
    assert all(d.payload["source"] == "llm" for d in decisions)
    assert all(r.decision_source == "llm" for r in state.decision_records)
    workflow = {t for spec in PHASE_SPECS.values() for t in spec.workflow_tools}
    assert all(t.source == "llm" for t in state.tool_results if t.tool not in workflow)
    assert {t.source for t in state.tool_results if t.tool in workflow} == {"workflow"}
    summary = json.loads((tmp_path / "l1" / "token_summary.json").read_text())
    assert summary["total"]["calls"] == len(records) and summary["total"]["input_tokens"] > 0
    assert set(summary["by_phase"]) == {p.value for p in Phase}


def test_llm_failures_fall_back_to_policy_and_still_complete(tmp_path):
    """AG-03: invalid LLM output → ≤ 2 retries → policy fallback, recorded per decision."""
    engine, logger = _llm_engine(tmp_path, "f1", always_invalid)
    runner = _runner(tmp_path, engine=engine)
    result = runner.start("", run_id="f1")
    assert result.status == RunStatus.COMPLETED
    records = logger.records()
    assert records and all(r.decision_source == "policy_fallback" for r in records)
    assert all(r.retries == 2 for r in records)
    state = runner.load_state("f1")
    assert {r.decision_source for r in state.decision_records} == {"policy_fallback"}


def test_llm_token_budget_exceeded_escalates(tmp_path):
    """AG-04: an LLM usage limit inside a phase escalates (no silent fallback)."""
    engine, _ = _llm_engine(tmp_path, "t1", policy_like)
    budgets = {Phase.P0: PhaseBudget(max_tokens=10)}
    result = _runner(tmp_path, engine=engine, budgets=budgets).start("", run_id="t1")
    assert result.status == RunStatus.ESCALATED and result.phase == Phase.P0
    assert "usage limit" in result.summary


def test_db30_interpretation_b_answer_is_consistent(tmp_path):
    """DB-30 'B' = simulate the available days with no outage (spec §2.4), not A's outage."""
    runner = _runner(tmp_path)
    runner.start("", run_id="db30", input_mode="pause")
    runner.resume("db30", "B")
    value = runner.load_state("db30").answer_values()["DB-30"]
    register = load_config("assumptions").get("DB-30").value
    days = register["doublet_available_days_per_yr"]
    assert value["interpretation"] == "B"
    assert value["planned_outage_days_per_doublet"] == 0
    assert value["demand_met_days_per_yr"] == value["doublet_available_days_per_yr"] == days

    runner.start("", run_id="db30a", input_mode="pause")
    runner.resume("db30a", "a")
    assert runner.load_state("db30a").answer_values()["DB-30"] == register


def test_llm_fallbacks_survive_verification_loop_backs(tmp_path):
    """run_summary counts the fallbacks of P6–P10 passes that a loop-back discarded (AG-03)."""
    engine, logger = _llm_engine(tmp_path, "fl", always_invalid)
    toolbox = stub_toolbox()
    toolbox.register(_verifier(DesignVerdict.REJECTED))
    runner = _runner(tmp_path, engine=engine, toolbox=toolbox)
    result = runner.start("", run_id="fl")
    assert result.status == RunStatus.ESCALATED and runner.load_state("fl").loop_count == 4
    fallbacks = [r for r in logger.records() if r.decision_source == "policy_fallback"]
    summary = json.loads((tmp_path / "fl" / "run_summary.json").read_text())
    assert summary["llm_fallbacks"] == len(fallbacks) > 0


def test_a_rejected_design_never_gets_a_final_selection_record(tmp_path):
    """DR-10 is not written for a design the P9 verifier REJECTED, even when the run escalates."""
    verdicts = iter([DesignVerdict.ACCEPTED, DesignVerdict.REJECTED] * 4)

    def func(args, context):
        verdict = next(verdicts)
        return ToolResult[VerificationOutput](
            data=VerificationOutput(
                candidate_id=context.selected_id,
                verdict=verdict,
                violations=["review failed"] if verdict == "REJECTED" else [],
                verifier="fake",
            ),
            summary=f"fake verifier: {verdict}",
        )

    toolbox = stub_toolbox()
    toolbox.register(ToolSpec("verify_design", "fake", CandidateArgs, func, owner="test"))
    runner = _runner(tmp_path, toolbox=toolbox)
    result = runner.start("", run_id="dr10")
    assert result.status == RunStatus.ESCALATED and "after 3 loops" in result.summary
    state = runner.load_state("dr10")
    assert "DR-10" not in {r.dr_id for r in state.decision_records}
    assert not (tmp_path / "dr10" / "decision_records" / "DR-10.json").exists()
    skipped = [e for e in runner.store("dr10").list_events("dr10") if e.type == "DR_SKIPPED"]
    assert len(skipped) == 4 and all("DR-10" in e.payload["message"] for e in skipped)


def test_llm_escalation_reason_with_numbers_never_reaches_the_summary(tmp_path):
    """AG-02: an LLM escalate step that states numbers is refused (retry, then policy)."""

    def escalate_in_p5(text):
        def func(messages, info):
            if output_name(info) == "Decision":
                context = json.loads(user_prompt(messages).split("CONTEXT:\n", 1)[1])
                if context["phase"] == "P5":
                    step = NextStep.model_construct(kind="escalate", rationale=text, args={})
                    body = {"phase": "P5", "steps": [step.model_dump()], "open_questions": []}
                    return ModelResponse(parts=[TextPart(json.dumps(body))])
            return policy_like(messages, info)

        return func

    leak = "Plant needs 4 doublets and LCOH 99 EUR/MWh"
    engine, _ = _llm_engine(tmp_path, "e1", escalate_in_p5(leak))
    result = _runner(tmp_path, engine=engine).start("", run_id="e1")
    assert result.status == RunStatus.COMPLETED
    summary = (tmp_path / "e1" / "run_summary.json").read_text()
    assert "99" not in summary and "EUR/MWh" not in summary

    engine, _ = _llm_engine(tmp_path, "e2", escalate_in_p5("No safe option; see TR-004."))
    result = _runner(tmp_path, engine=engine).start("", run_id="e2")
    assert result.status == RunStatus.ESCALATED and "see TR-004" in result.summary


def test_llm_gap_question_with_numbers_is_not_shown(tmp_path):
    """AG-02: an LLM request_input question is a fallback only, and never one with numbers."""
    runner = _runner(tmp_path)
    runner.start("", run_id="q1", input_mode="assume")
    state = runner.load_state("q1")
    state.gap_questions = {}
    title = load_config("assumptions").get("DB-31").title
    request = runner._gap_request(state, Phase.P1, "DB-31", "Is a 55 degC return acceptable?")
    assert request.question == title
    request = runner._gap_request(state, Phase.P1, "DB-31", "Which DH return temperature?")
    assert request.question == "Which DH return temperature?"


class _SizedOut(StrictModel):
    shortlist: list[str]
    kpi_evidence: bool
    candidates: list[Candidate]
    kpis: list[KPISet]


class _UqOut(StrictModel):
    kpis: list[KPISet]


def test_reducer_adds_sized_designs_and_only_uncertainty_percentiles(tmp_path):
    """WP-14 reducer: P6's sized design joins the run (by id, once) with its KPIs; P8 adds
    percentiles without touching the other KPIs of the selected design."""

    def optimise(args, context):
        base = next(c for c in context.candidates if c.concept == "C2")
        sized = base.model_copy(update={"id": "C2-opt", "design_flow_m3h": 250.0})
        kpi = KPISet(candidate_id="C2-opt", lcoh_per_mwh=30.0, lcoc_per_mwh=20.0)
        data = _SizedOut(shortlist=["C2-opt"], kpi_evidence=True, candidates=[sized], kpis=[kpi])
        return ToolResult[_SizedOut](data=data, summary="fake TL-13")

    def uncertainty(args, context):
        pct = {"lcoh_per_mwh": {"p10": 28.0, "p50": 30.5, "p90": 34.0}}
        kpi = KPISet(candidate_id=context.selected_id, percentiles=pct)
        return ToolResult[_UqOut](data=_UqOut(kpis=[kpi]), summary="fake TL-15")

    toolbox = stub_toolbox()
    toolbox.register(ToolSpec("optimise_design", "fake", OptimiseArgs, optimise, owner="test"))
    toolbox.register(ToolSpec("uncertainty", "fake", CandidateArgs, uncertainty, owner="test"))
    runner = _runner(tmp_path, toolbox=toolbox)
    result = runner.start("", run_id="sized")
    state = runner.load_state("sized")
    assert result.status == RunStatus.COMPLETED and state.selected_id == "C2-opt"
    ids = [c.id for c in state.candidates]
    assert ids.count("C2-opt") == 1  # replaced by id, never duplicated
    kpi = state.kpis["C2-opt"]
    assert kpi.lcoh_per_mwh == 30.0 and kpi.lcoc_per_mwh == 20.0
    assert kpi.percentiles["lcoh_per_mwh"]["p50"] == 30.5


# --------------------------------------------------------------------------- L-1 follow-up
def test_kind_errors_are_coerced_and_counted_not_fallbacks(tmp_path):
    """Fix 1: reserved tools sent as call_tool run as their kind; each repair is an event."""
    engine, logger = _llm_engine(tmp_path, "k1", kinds_as_call_tool)
    runner = _runner(tmp_path, engine=engine)
    result = runner.start("", run_id="k1")
    assert result.status == RunStatus.COMPLETED
    state = runner.load_state("k1")
    assert state.fallback_count() == 0
    assert {r.decision_source for r in logger.records()} == {"llm"}
    events = runner.store("k1").list_events("k1")
    coerced = [e.payload for e in events if e.type == "KIND_COERCED"]
    assert {(c["tool"], c["to_kind"]) for c in coerced} == {
        ("request_missing_input", "request_input"),
        ("generate_candidates", "propose_candidates"),
        ("design_guard", "verify"),
        ("optimise_design", "run_optimisation"),
        ("verify_design", "verify"),
    }
    assert all(c["from_kind"] == "call_tool" for c in coerced)
    assert not [e for e in events if e.type == "STEP_REJECTED"]


def test_duplicate_tool_calls_are_rejected_without_using_tool_budget(tmp_path):
    """Fix 3 (rule 4): L-1 re-ran read_results with the same arguments until P9's budget ran out.

    The fake reviewer ignores the feedback: the first repeat is rejected with feedback it can
    see in its history; the second ends its turn and the runner closes P9 deterministically.
    """
    p9_contexts = []

    def repeat_read_results(messages, info):
        if output_name(info) == "Decision":
            context = json.loads(user_prompt(messages).split("CONTEXT:\n", 1)[1])
            if context["phase"] == "P9":
                p9_contexts.append(context)
                step = NextStep(
                    kind="call_tool",
                    tool="read_results",
                    args={"candidate_id": "C1-3d"},
                    rationale="read the results",
                )
                body = Decision(phase=Phase.P9, steps=[step]).model_dump_json()
                return ModelResponse(parts=[TextPart(body)])
        return policy_like(messages, info)

    engine, _ = _llm_engine(tmp_path, "d1", repeat_read_results)
    runner = _runner(tmp_path, engine=engine)
    result = runner.start("", run_id="d1")
    assert result.status == RunStatus.COMPLETED  # was: tool-call budget exceeded in P9
    state = runner.load_state("d1")
    p9 = [r for r in state.tool_results if r.phase == Phase.P9]
    assert [(r.tool, r.source) for r in p9] == [
        ("read_results", "llm"),
        ("verify_design", "workflow"),  # the runner closes the phase
    ]
    assert state.progress["P9"].tool_calls_used == 2
    assert state.progress["P9"].duplicate_rejections == 2 and len(p9_contexts) == 3
    rejected = [
        e.payload["message"]
        for e in runner.store("d1").list_events("d1")
        if e.type == "STEP_REJECTED"
    ]
    assert len(rejected) == 2 and all("duplicate call: read_results" in m for m in rejected)
    assert p9[0].id in rejected[0] and "closes the phase" in rejected[1]
    # AG-08 keeps decisions out of the reviewer's history, but it sees its own rejection.
    seen = [h for h in p9_contexts[2]["history"] if h["type"] == "STEP_REJECTED"]
    assert seen and "duplicate call" in seen[0]["message"]
    assert not [h for h in p9_contexts[2]["history"] if h["type"] == "DECISION"]


def test_a_tool_may_rerun_once_the_state_it_reads_changed(tmp_path):
    """Fix 3 (review): design_guard before and after generate_candidates is not a duplicate."""

    class GuardFirst(PolicyEngine):
        def __init__(self):
            self.script = [
                NextStep(
                    kind="verify", tool="design_guard", args={"candidate_id": "all"}, rationale="x"
                ),
                NextStep(kind="propose_candidates", tool="generate_candidates", rationale="x"),
                NextStep(
                    kind="verify", tool="design_guard", args={"candidate_id": "all"}, rationale="x"
                ),
            ]

        def decide(self, context):
            if context.phase == Phase.P3 and self.script:
                step = self.script.pop(0)
                return EngineResult(Decision(phase=Phase.P3, steps=[step]), source="policy")
            return super().decide(context)

    runner = _runner(tmp_path, engine=GuardFirst())
    assert runner.start("", run_id="s3").status == RunStatus.COMPLETED
    state = runner.load_state("s3")
    guards = [r for r in state.tool_results if r.tool == "design_guard"]
    assert len(guards) == 2 and guards[0].signature != guards[1].signature
    assert not guards[0].candidate_ids and guards[1].candidate_ids  # the second one checked them
    assert not [e for e in runner.store("s3").list_events("s3") if e.type == "STEP_REJECTED"]


def test_duplicate_guard_scope(tmp_path):
    """Fix 3: what counts as the same call — normalised args, same state, same loop.

    A failed call counts (same inputs, same failure). A required tool not yet completed in the
    phase pass may repeat an earlier phase's call once (P9 re-verifies after P7).
    """
    runner = _runner(tmp_path)
    result = runner.start("", run_id="g1")
    assert result.status == RunStatus.COMPLETED
    state = runner.load_state("g1")
    verify = [r for r in state.tool_results if r.tool == "verify_design"]
    assert [(r.phase, r.source) for r in verify] == [(Phase.P7, "policy"), (Phase.P9, "policy")]
    # Pre-L-2: P8's robust_backup step and DR-11 change the run state between P7 and P9.
    assert verify[0].signature != verify[1].signature
    assert not [e for e in runner.store("g1").list_events("g1") if e.type == "STEP_REJECTED"]

    def record(tool, args, phase, ok=True):
        state.tool_results.append(
            ToolRecord(
                id=f"TR-{len(state.tool_results) + 1:03d}",
                tool=tool,
                phase=phase,
                args=args,
                ok=ok,
                source="llm",
                signature=runner._signature(state, tool, args),
                loop=state.loop_count,
            )
        )
        return state.tool_results[-1]

    state.progress.clear()
    p7, p9 = PHASE_SPECS[Phase.P7], PHASE_SPECS[Phase.P9]
    sim = record("simulate_annual", {}, Phase.P7)
    # defaults filled in: {} == {'candidate_id': 'all', 'mode': 'fast'}
    assert runner._duplicate_of(state, p7, "simulate_annual", {"mode": "fast"}) == sim
    assert runner._duplicate_of(state, p7, "simulate_annual", {"mode": "full"}) is None
    failed = record("knowledge_search", {"query": "plate HEX"}, Phase.P7, ok=False)
    assert runner._duplicate_of(state, p7, "knowledge_search", {"query": "plate HEX"}) == failed
    # P9 has not run verify_design in this pass: it may repeat P7's call once, then not again.
    state.tool_results = [r for r in state.tool_results if r.tool != "verify_design"]
    record("verify_design", {"candidate_id": "$selected"}, Phase.P7)
    assert runner._duplicate_of(state, p9, "verify_design", {"candidate_id": "C1-3d"}) is None
    p9_verify = record("verify_design", {"candidate_id": "C1-3d"}, Phase.P9)
    assert runner._duplicate_of(state, p9, "verify_design", {"candidate_id": "C1-3d"}) == p9_verify
    # Changed state (new KPI evidence) or a new verification loop: no longer the same call.
    kpis = dict(state.kpis)
    state.kpis["C1-3d"] = KPISet(candidate_id="C1-3d", lcoh_per_mwh=50.0)
    assert runner._duplicate_of(state, p7, "simulate_annual", {}) is None
    state.kpis = kpis
    assert runner._duplicate_of(state, p7, "simulate_annual", {}) == sim
    state.loop_count += 1
    assert runner._duplicate_of(state, p7, "simulate_annual", {}) is None


# --------------------------------------------------------------------------- DR-11 (pre-L-2)
def _robust_toolbox(reject_robust: bool = False):
    """Stub toolbox + fake TL-13 (C2-opt), a fake robust_backup step and a stub verifier."""
    from gemini_agent.runner.real_tools import RobustBackupToolOutput

    def optimise(args, context):
        base = next(c for c in context.candidates if c.concept == "C2")
        sized = base.model_copy(update={"id": "C2-opt", "equipment_sizes": {"peak_boiler_mw": 5.0}})
        kpi = KPISet(candidate_id="C2-opt", lcoh_per_mwh=42.5, lcoc_per_mwh=26.3)
        data = _SizedOut(shortlist=["C2-opt"], kpi_evidence=True, candidates=[sized], kpis=[kpi])
        return ToolResult[_SizedOut](data=data, summary="fake TL-13")

    def robust_backup(args, context):
        installed = next(c for c in context.candidates if c.id == args.candidate_id)
        variant = installed.model_copy(
            update={"id": f"{installed.id}-robust", "equipment_sizes": {"peak_boiler_mw": 10.9}}
        )
        data = RobustBackupToolOutput(
            candidate_id=installed.id,
            robust_candidate_id=variant.id,
            robust_backup_percentile=90,
            installed_backup_mw={"peak_boiler_mw": 5.0},
            robust_backup_mw={"peak_boiler_mw": 10.9},
            added_backup_mw={"peak_boiler_mw": 5.9},
            probability_demand_met=0.46,
            probability_demand_met_robust=0.9,
            year_mode="full",
            delta_lcoh_per_mwh=2.05,
            delta_lcoc_per_mwh=0.0,
            delta_lcoh_plus_lcoc_per_mwh=2.05,
            delta_capex_million=2.2,
            robust_meets_demand=True,
            candidates=[variant],
            kpis=[KPISet(candidate_id=variant.id, lcoh_per_mwh=44.55, lcoc_per_mwh=26.3)],
        )
        return ToolResult[RobustBackupToolOutput](data=data, summary="fake robust_backup")

    def verify(args, context):
        candidate = context.selected_id if args.candidate_id == "all" else args.candidate_id
        verdict = "REJECTED" if reject_robust and candidate.endswith("-robust") else "UNVERIFIED"
        return ToolResult[VerificationOutput](
            data=VerificationOutput(
                candidate_id=candidate,
                verdict=verdict,
                violations=["boiler"] if verdict == "REJECTED" else [],
                verifier="fake",
            ),
            summary=f"fake verifier: {verdict}",
        )

    toolbox = stub_toolbox()
    toolbox.register(ToolSpec("optimise_design", "fake", OptimiseArgs, optimise, owner="test"))
    toolbox.register(
        ToolSpec("robust_backup", "fake", CandidateArgs, robust_backup, owner="test", stub=False)
    )
    toolbox.register(ToolSpec("verify_design", "fake", CandidateArgs, verify, owner="test"))
    return toolbox


def test_dr11_installs_the_robust_backups_and_p9_verifies_that_design(tmp_path):
    """The workflow step runs after P8's tools; the DR-11 choice selects the design for P9."""
    runner = _runner(tmp_path, toolbox=_robust_toolbox())
    result = runner.start("", run_id="rb")
    state = runner.load_state("rb")
    assert result.status == RunStatus.COMPLETED and result.selected_id == "C2-opt-robust"
    p8 = [r for r in state.tool_results if r.phase == Phase.P8]
    assert [(r.tool, r.source) for r in p8] == [
        ("uncertainty", "policy"),
        ("sensitivity", "policy"),
        ("robust_backup", "workflow"),
    ]
    assert state.robust_backup.tool_result_id == p8[-1].id
    assert state.progress["P8"].tool_calls_used == 2  # the workflow step is not the agent's
    dr11 = next(r for r in state.decision_records if r.dr_id == "DR-11")
    assert (dr11.choice, dr11.criterion.id) == ("p90-robust-backup", "opt-robust-backup")
    assert state.shortlist[0] == "C2-opt-robust" and "C2-opt" in state.shortlist
    assert state.kpis["C2-opt-robust"].lcoh_per_mwh == 44.55
    assert state.analysed_id("C2-opt-robust") == "C2-opt"
    events = runner.store("rb").list_events("rb")
    selected = [e.payload for e in events if e.type == "DESIGN_SELECTED"]
    assert selected == [
        {
            "candidate_id": "C2-opt-robust",
            "replaces": "C2-opt",
            "dr_id": "DR-11",
            "message": "DR-11 p90-robust-backup: selected C2-opt-robust instead of C2-opt; the P9 "
            "DesignVerifier verifies it",
        }
    ]
    p9 = [r for r in state.tool_results if r.phase == Phase.P9 and r.tool == "verify_design"]
    assert [r.args["candidate_id"] for r in p9] == ["C2-opt-robust"]
    assert next(r for r in state.decision_records if r.dr_id == "DR-10").choice == "C2-opt-robust"
    assert result.verdict == "UNVERIFIED"  # stub verifier: never ACCEPTED (golden rule 3)


def test_dr11_rejected_variant_loops_back_and_keeps_the_installed_backups(tmp_path):
    """A variant the P9 verifier REJECTs is excluded: the next pass re-decides DR-11."""
    runner = _runner(tmp_path, toolbox=_robust_toolbox(reject_robust=True))
    result = runner.start("", run_id="rr")
    state = runner.load_state("rr")
    assert result.status == RunStatus.COMPLETED and result.selected_id == "C2-opt"
    assert state.loop_count == 1 and "C2-opt-robust" in state.excluded_ids()
    events = runner.store("rr").list_events("rr")
    choices = [e.payload["choice"] for e in events if e.type == "DECISION_RECORD"]
    assert [c for c in choices if c.endswith("backup")] == ["p90-robust-backup", "installed-backup"]
    dr11 = next(r for r in state.decision_records if r.dr_id == "DR-11")
    assert dr11.choice == "installed-backup" and "excluded" in dr11.options[1].notes


def test_dr11_llm_may_keep_the_installed_backups_on_the_cost_criterion(tmp_path):
    """The LLM decides DR-11 on the offered evidence: keeping the backups is a valid choice."""

    def keeps_backups(messages, info):
        if output_name(info) == "OptionChoice" and "DR-11" in user_prompt(messages)[:20]:
            choice = {
                "dr_id": "DR-11",
                "criterion": "lowest-delta_lcoh_plus_lcoc_per_mwh",
                "option_id": "installed-backup",
                "rationale": "lowest delta_lcoh_plus_lcoc_per_mwh in robust_backup: the base-year "
                "backups add no cost",
                "rejected": [{"option_id": "p90-robust-backup", "reason": "adds backup cost"}],
            }
            return ModelResponse(parts=[TextPart(json.dumps(choice))])
        return policy_like(messages, info)

    engine, logger = _llm_engine(tmp_path, "lk", keeps_backups)
    toolbox = _robust_toolbox()
    runner = _runner(tmp_path, engine=engine, toolbox=toolbox)
    result = runner.start("", run_id="lk")
    state = runner.load_state("lk")
    assert result.status == RunStatus.COMPLETED and result.selected_id == "C2-opt"
    dr11 = next(r for r in state.decision_records if r.dr_id == "DR-11")
    assert (dr11.choice, dr11.decision_source) == ("installed-backup", "llm")
    assert dr11.criterion.key == "delta_lcoh_plus_lcoc_per_mwh"
    assert state.fallback_count() == 0
    assert {r.decision_source for r in logger.records()} == {"llm"}
    events = runner.store("lk").list_events("lk")
    assert not [e for e in events if e.type == "DESIGN_SELECTED"]
    md = (tmp_path / "lk" / "decision_records" / "DR-11.md").read_text("utf-8")
    assert "the chosen option is installed and verified in P9" in md


# --------------------------------------------------------------------------- L-1 P0 follow-up
# Recorded P0 answers of the real Ollama S11 runs (branch l1-p0-evidence, llm_calls.jsonl): three
# of five models never said finalise in P0 and were escalated ("step budget exceeded in P0").
P0_REPLAYS: dict[str, list[dict]] = {
    "qwen3.6:35b-a3b": [
        {
            "kind": "delegate",
            "rationale": "The request is in scope and the objective is met; proceed to the next "
            "phase.",
            "expected_outcome": "Transition to P1",
        },
        {
            "kind": "delegate",
            "rationale": "The user prompt is in scope and complete. No further intake actions are "
            "needed; proceed to the next phase.",
            "expected_outcome": "Transition to P1.",
        },
        {
            "kind": "delegate",
            "rationale": "The request is in scope and explicitly asks to select, size, and "
            "validate a system. The objective of Intake is to confirm this alignment. Since the "
            "prompt confirms the domain and intent, we delegate to proceed to the next phase.",
            "expected_outcome": "Transition to P1 or subsequent design phases.",
        },
    ],
    "gemma4:26b-a4b": [
        {
            "kind": "delegate",
            "rationale": "The request is in scope and aligns with the geothermal design objective. "
            "No further intake checks are required.",
            "expected_outcome": "Transition to the next phase.",
        },
        {
            "kind": "delegate",
            "rationale": "The user request is within scope and provides a clear objective for the "
            "design study. No further intake validation is required.",
            "expected_outcome": "Transition to the next phase.",
        },
        {
            "kind": "delegate",
            "rationale": "The request is in scope and aligns with the design objective. The intake "
            "phase is complete.",
            "expected_outcome": "Transition to the next phase.",
        },
    ],
    "qwen3.5:9b-q4_K_M": [
        {
            "kind": "call_tool",
            "tool": "knowledge_search",
            "args": {
                "query": "deep-geothermal district heating cooling 100 degC 20 degC GEMINI "
                "digital twin",
                "limit": 3,
            },
            "rationale": "Confirm system design intent before proceeding.",
            "expected_outcome": "",
        },
        {
            "kind": "delegate",
            "rationale": "Request is in scope; proceed to candidate generation phase.",
            "expected_outcome": "Transition to next workflow phase.",
        },
        {
            "kind": "delegate",
            "rationale": "Phase objective met; request is in scope and prior search validated "
            "domain context.",
            "expected_outcome": "Proceed to P1 workflow.",
        },
    ],
}


def _replay_p0(steps: list[dict]):
    """FunctionModel function: the recorded P0 answers in order (last one repeated), else policy."""
    answers = list(steps)

    def func(messages, info):
        if output_name(info) == "Decision" and user_prompt(messages).startswith("PHASE P0 "):
            step = answers.pop(0) if len(answers) > 1 else answers[0]
            decision = {"phase": "P0", "steps": [step], "open_questions": []}
            return ModelResponse(parts=[TextPart(json.dumps(decision))])
        return policy_like(messages, info)

    return func


def _p0_events(runner: DesignRunner, run_id: str, event_type: str) -> list:
    events = runner.store(run_id).list_events(run_id)
    return [e for e in events if e.type == event_type and e.phase == "P0"]


@pytest.mark.parametrize("model", sorted(P0_REPLAYS))
def test_p0_replay_of_an_in_scope_request_reaches_p1(tmp_path, model):
    """L-1 P0 follow-up: the recorded P0 answers (delegate / a tool) no longer escalate."""
    run_id = model.replace(":", "_")
    engine, _ = _llm_engine(tmp_path, run_id, _replay_p0(P0_REPLAYS[model]))
    runner = _runner(tmp_path, engine=engine)
    result = runner.start("", run_id=run_id)
    assert result.status == RunStatus.COMPLETED, result.summary
    starts = _phase_starts(runner, run_id)
    assert starts[:2] == ["P0", "P1"]
    assert not _p0_events(runner, run_id, "ESCALATE")
    rejected = _p0_events(runner, run_id, "STEP_REJECTED")
    assert rejected and all(
        "only valid steps are finalise" in e.payload["message"] for e in rejected
    )
    closed = _p0_events(runner, run_id, "INTAKE_CLOSED")
    assert len(closed) == 1 and closed[0].payload["in_scope"] is True
    assert "step budget exceeded in P0 (3 steps)" in closed[0].payload["message"]
    assert not _p0_events(runner, run_id, "TOOL_RESULT")
    state = runner.load_state(run_id)
    prog = state.phase_progress(Phase.P0)
    assert prog.tool_calls_used == 0 and prog.steps_used == PHASE_SPECS[Phase.P0].budget.max_steps


def test_p0_replay_of_an_off_topic_request_is_still_refused(tmp_path):
    """S3 with a model that never says refuse: the deterministic intake refuses, no tool runs."""
    engine, _ = _llm_engine(tmp_path, "s3", _replay_p0(P0_REPLAYS["gemma4:26b-a4b"]))
    runner = _runner(tmp_path, engine=engine)
    result = runner.start("What's the best pizza in Delft?", run_id="s3")
    assert result.status == RunStatus.REFUSED and result.phase == Phase.P0
    assert "please ask about the design" in result.summary
    assert _phase_starts(runner, "s3") == ["P0"]
    closed = _p0_events(runner, "s3", "INTAKE_CLOSED")
    assert len(closed) == 1 and closed[0].payload["in_scope"] is False
    assert runner.load_state("s3").phase_progress(Phase.P0).tool_calls_used == 0


@pytest.mark.parametrize(
    ("prompt", "conflict"),
    [
        ("Supply heating at 120 °C", "(DB-16)"),
        (
            (
                "Design the geothermal heating and cooling system. The geothermal gradient is "
                "30 °C/km but the reservoir is 150 °C at 2.5 km."
            ),
            "is inconsistent with the design basis",
        ),
    ],
    ids=["S5", "S9"],
)
def test_p0_replay_keeps_the_constraint_conflict_check(tmp_path, prompt, conflict):
    """S5/S9: the conflicts the intake records are the same with the replayed P0 answers."""
    policy = _runner(tmp_path)
    policy.start(prompt, run_id="pol")
    expected = policy.load_state("pol").conflicts
    assert expected and any(conflict in text for text in expected)
    engine, _ = _llm_engine(tmp_path, "rep", _replay_p0(P0_REPLAYS["qwen3.6:35b-a3b"]))
    replay = _runner(tmp_path, engine=engine)
    result = replay.start(prompt, run_id="rep")
    assert result.status == RunStatus.COMPLETED and "P1" in _phase_starts(replay, "rep")
    assert replay.load_state("rep").conflicts == expected
    assert not _p0_events(replay, "rep", "ESCALATE")


class _ScriptedEngine(PolicyEngine):
    """Policy engine whose P0 decisions are scripted (one step each); records the P0 contexts."""

    name = "scripted"

    def __init__(self, p0_steps: list[NextStep]) -> None:
        self.p0_steps = list(p0_steps)
        self.contexts = []

    def decide(self, context):
        if context.phase == Phase.P0 and self.p0_steps:
            self.contexts.append(context)
            step = self.p0_steps.pop(0)
            return EngineResult(Decision(phase=Phase.P0, steps=[step]), source="llm")
        return super().decide(context)


def test_p0_rejects_every_other_kind_and_tool_then_closes_from_the_intake(tmp_path):
    """Runner level: tools, delegate and escalate are rejected in P0; the intake closes it."""
    steps = [
        NextStep(
            kind="call_tool",
            tool="knowledge_search",
            args={"query": "scope"},
            rationale="Look it up.",
        ),
        NextStep(kind="delegate", rationale="Hand over to the next phase."),
        NextStep(kind="escalate", rationale="Unsure how to confirm the scope."),
    ]
    engine = _ScriptedEngine(steps)
    runner = _runner(tmp_path, engine=engine)
    result = runner.start("", run_id="k1")
    assert result.status == RunStatus.COMPLETED
    rejected = _p0_events(runner, "k1", "STEP_REJECTED")
    assert [e.payload["step"]["kind"] for e in rejected] == ["call_tool", "delegate", "escalate"]
    assert all("no tool runs in intake" in e.payload["message"] for e in rejected)
    assert len(_p0_events(runner, "k1", "INTAKE_CLOSED")) == 1
    assert not _p0_events(runner, "k1", "ESCALATE") and not _p0_events(runner, "k1", "TOOL_RESULT")
    # The P0 context tells the model what is valid and offers no tool.
    assert engine.contexts and all(c.allowed_tools == [] for c in engine.contexts)
    assert "finalise if in_scope is true or refuse if it is false" in engine.contexts[0].objective
    # The second P0 context shows the feedback of the first rejection.
    assert any(
        h["type"] == "STEP_REJECTED" and "only valid steps" in h["message"]
        for h in engine.contexts[1].history
    )


def test_p0_finalise_and_refuse_still_close_the_phase_without_the_intake_event(tmp_path):
    """A model that answers as asked: finalise (in scope) → P1; refuse (off-topic) → refused."""
    engine = _ScriptedEngine([NextStep(kind="finalise", rationale="In scope.")])
    runner = _runner(tmp_path, engine=engine)
    assert runner.start("", run_id="f1").status == RunStatus.COMPLETED
    assert not _p0_events(runner, "f1", "INTAKE_CLOSED")
    assert not _p0_events(runner, "f1", "STEP_REJECTED")
    engine = _ScriptedEngine([NextStep(kind="refuse", rationale="Off-topic.")])
    runner = _runner(tmp_path, engine=engine)
    result = runner.start("Best pizza in Delft?", run_id="f2")
    assert result.status == RunStatus.REFUSED and not _p0_events(runner, "f2", "INTAKE_CLOSED")


def test_policy_engine_p0_is_unchanged(tmp_path):
    """The policy engine finalises P0 on its first step: no rejection, no intake close."""
    runner = _runner(tmp_path)
    assert runner.start("", run_id="p0").status == RunStatus.COMPLETED
    decisions = _p0_events(runner, "p0", "DECISION")
    assert [s["kind"] for d in decisions for s in d.payload["steps"]] == ["finalise"]
    assert not _p0_events(runner, "p0", "STEP_REJECTED")
    assert not _p0_events(runner, "p0", "INTAKE_CLOSED")
    assert runner.load_state("p0").phase_progress(Phase.P0).steps_used == 1


def test_step_budget_still_escalates_outside_p0(tmp_path):
    """AG-04 is unchanged after P0: a used-up step budget in P1 escalates."""
    budgets = {Phase.P1: PhaseBudget(max_steps=1)}
    engine = _ScriptedEngine([])
    result = _runner(tmp_path, engine=engine, budgets=budgets).start("", run_id="b3")
    assert result.status == RunStatus.ESCALATED and result.phase == Phase.P1
    assert "step budget exceeded in P1 (1 steps)" in result.summary
