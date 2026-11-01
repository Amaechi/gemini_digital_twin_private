"""Unit tests for gemini_agent.runner.design_runner (spec §5.2, AG-03…AG-12) — offline only."""

import json

import pytest

from gemini_agent.agents.engine import LLMEngine, PolicyEngine, ResilientEngine
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.runner.design_runner import DesignRunner, InvalidAnswer
from gemini_agent.runner.phases import PhaseBudget
from gemini_agent.runner.tool_stubs import VerificationOutput
from gemini_agent.runner.toolbox import CandidateArgs, ToolSpec, default_toolbox
from gemini_agent.schemas.common import ToolResult
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import DR_TITLES, DesignVerdict, Phase, RunStatus
from unit_test.gemini_agent.llm_fakes import (
    Counting,
    always_invalid,
    fake_model,
    policy_like,
)


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
    assert len(list((run_dir / "decision_records").glob("DR-*.json"))) == 10
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
    toolbox = default_toolbox()
    toolbox.register(_verifier(DesignVerdict.REJECTED))
    runner = _runner(tmp_path, toolbox=toolbox)
    result = runner.start("", run_id="v1")
    assert result.status == RunStatus.ESCALATED and "after 3 loops" in result.summary
    state = runner.load_state("v1")
    assert state.loop_count == 4 and state.violations == ["brine cooling 45 K > 40 K"]
    starts = _phase_starts(runner, "v1")
    assert starts.count("P6") == 4 and starts.count("P8") == 0


def test_only_a_real_verifier_can_accept(tmp_path):
    """AG-11: ACCEPTED comes from the verifier; a stub claiming ACCEPTED is downgraded."""
    toolbox = default_toolbox()
    toolbox.register(_verifier(DesignVerdict.ACCEPTED))
    result = _runner(tmp_path, toolbox=toolbox).start("", run_id="ok")
    assert result.status == RunStatus.COMPLETED and result.verdict == "ACCEPTED"

    toolbox.register(_verifier(DesignVerdict.ACCEPTED, stub=True))
    result = _runner(tmp_path, toolbox=toolbox).start("", run_id="stub")
    assert result.verdict == "UNVERIFIED"


def _llm_engine(tmp_path, run_id, func):
    logger = LLMLogger(tmp_path / run_id)
    llm = LLMEngine(
        load_config("llm"),
        model=fake_model(func),
        logger=logger,
        run_id=run_id,
        validate_args=default_toolbox().validate_args,
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
    assert all(t.source == "llm" for t in state.tool_results)
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
