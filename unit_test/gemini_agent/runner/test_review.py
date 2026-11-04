"""Tests of the interactive design review loop (``cli run --review``; FINAL_SPRINT §E) — offline.

The what-if tool is replaced by a fake (``unit_test.gemini_agent.whatif_fakes``) that keeps the
real lever parsing, bounds and DesignGuard (``apply_levers``) but fabricates the KPIs, so the
runner tests stay on the stub toolbox. One
test at the end runs the real tools (smoke budget) end to end: objection → what-if → adopt →
TL-13 re-sized from the adopted design → ACCEPTED.
"""

from __future__ import annotations

import json

import pytest
from pydantic_ai.messages import ModelResponse, TextPart

from gemini_agent.agents.engine import LLMEngine, PolicyEngine, ResilientEngine
from gemini_agent.agents.objections import map_objection_by_keywords
from gemini_agent.cli import EXIT_OK, main
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.runner import review as review_mod
from gemini_agent.runner.design_runner import DesignRunner, InvalidAnswer
from gemini_agent.runner.real_tools import SESSION_DIR, real_toolbox
from gemini_agent.runner.tool_stubs import stub_toolbox
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import (
    REVIEW_DR_ID,
    CandidateBrief,
    DesignVerdict,
    LeverBrief,
    LeverChoice,
    Phase,
    ReviewQuestion,
    RunStatus,
    check_lever_choice,
)
from gemini_agent.tools import whatif as whatif_tool
from unit_test.gemini_agent.llm_fakes import (
    fake_model,
    output_name,
    policy_like,
    user_prompt,
)
from unit_test.gemini_agent.whatif_fakes import fake_whatif

pytestmark = pytest.mark.usefixtures("stub_tools")

REVIEW_EVENTS = (
    "REVIEW_START",
    "REVIEW_OBJECTION",
    "REVIEW_MAPPED",
    "REVIEW_UNMATCHED",
    "REVIEW_REFUSED",
    "REVIEW_WHATIF",
    "REVIEW_ADOPTED",
    "REVIEW_KEPT",
    "REVIEW_NONE",
)


# --------------------------------------------------------------------------- helpers
class Script:
    """``ask_fn`` answering by request id (Enter = '' for everything else); keeps the questions."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = dict(answers)
        self.asked: list[tuple[str, str]] = []

    def __call__(self, request):
        self.asked.append((request.request_id, request.question))
        return self.answers.get(request.request_id, "")

    def ids(self, prefix: str = "") -> list[str]:
        return [rid for rid, _ in self.asked if rid.startswith(prefix)]


def _events(runner: DesignRunner, run_id: str):
    return runner.store(run_id).list_events(run_id)


def _types(runner: DesignRunner, run_id: str) -> list[str]:
    return [e.type for e in _events(runner, run_id)]


def _dr12(runner: DesignRunner, run_id: str):
    state = runner.load_state(run_id)
    return next(r for r in state.decision_records if r.dr_id == REVIEW_DR_ID)


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


# --------------------------------------------------------------------------- keyword mapping
def _question(objection: str, **kwargs) -> ReviewQuestion:
    return ReviewQuestion(
        run_id="q",
        round=1,
        objection=objection,
        selected=CandidateBrief(id="C2-opt", concept="C2", doublets=2, depth_m=2500.0),
        levers=review_mod.lever_briefs(),
        candidates=[
            CandidateBrief(id="C2-2d", concept="C2", doublets=2, depth_m=2500.0),
            CandidateBrief(id="C3-2d", concept="C3", doublets=2, depth_m=2500.0),
            CandidateBrief(id="C4-2d", concept="C4", doublets=2, depth_m=3000.0),
            CandidateBrief(id="C5-2d", concept="C5", doublets=2, depth_m=2000.0),
        ],
        **kwargs,
    )


@pytest.mark.parametrize(
    ("objection", "lever", "value"),
    [
        ("the boiler is too big", "OPT-ROBUST-BACKUP", "off"),
        ("I want more backup capacity", "OPT-ROBUST-BACKUP", "on"),
        ("return temperature 65", "DB-31", "65"),
        ("set the heating return to 60 degC", "DB-31", "60"),
        ("cooling return 26", "DB-32", "26"),
        ("I'd prefer 3 doublets", "doublets", "3"),
        ("one more doublet please", "doublets", "3"),
        ("fewer doublets", "doublets", "1"),
        ("drill deeper", "concept", "C4-2d"),
        ("shallower wells", "concept", "C5-2d"),
        ("depth 3 km", "concept", "C4-2d"),
        ("try C3 instead", "concept", "C3-2d"),
        ("use absorption chillers", "concept", "C3-2d"),
        ("flow 200 m3/h per doublet", "design_flow_m3h", "200"),
        ("use DB-30 interpretation B", "DB-30", "B"),
    ],
)
def test_keyword_mapping_maps_the_named_levers(objection, lever, value):
    """Policy mapping: boiler/backup, return temperature, doublets, depth, flow, concept, DB-30."""
    question = _question(objection)
    choice = map_objection_by_keywords(question)
    assert (choice.lever, choice.value) == (lever, value), choice.rationale
    assert check_lever_choice(question, choice) is None


@pytest.mark.parametrize(
    "objection",
    ["make it prettier", "the boiler", "change the return temperature", "deeper"],
)
def test_keyword_mapping_says_no_lever_matches(objection):
    """Vague objections map to no lever (the runner asks again); 'deeper' with no deeper candidate."""
    question = _question(objection)
    if objection == "deeper":
        question = question.model_copy(update={"candidates": question.candidates[:2]})
    choice = map_objection_by_keywords(question)
    assert choice.lever is None and choice.value is None and choice.rationale


def test_check_lever_choice_rejects_unknown_levers_and_bad_values():
    """The validator the LLM agent and the runner share."""
    question = _question("x")
    assert check_lever_choice(question, LeverChoice(lever=None, rationale="none")) is None
    bad = [
        (LeverChoice(lever="DB-99", value="1", rationale="r"), "offered ids"),
        (LeverChoice(lever="DB-31", value="hot", rationale="r"), "needs a number"),
        (LeverChoice(lever="doublets", value="2.5", rationale="r"), "integer"),
        (LeverChoice(lever="DB-30", value="C", rationale="r"), "one of"),
        (LeverChoice(lever="OPT-ROBUST-BACKUP", value="maybe", rationale="r"), "on or off"),
        (LeverChoice(lever="concept", value="C9", rationale="r"), "screened candidate"),
    ]
    for choice, text in bad:
        error = check_lever_choice(question, choice)
        assert error and text in error, (choice, error)
    with pytest.raises(ValueError, match="needs a value"):
        LeverChoice(lever="DB-31", rationale="r")
    with pytest.raises(ValueError, match="do not state numbers"):
        LeverChoice(lever="DB-31", value="65", rationale="set 65 degC")


def test_lever_briefs_mirror_the_whatif_catalogue():
    """Every what-if lever is offered; the adoptable flag marks the design levers."""
    briefs = {b.id: b for b in review_mod.lever_briefs()}
    assert set(briefs) == set(whatif_tool.LEVERS)
    assert {b.id for b in briefs.values() if b.adoptable} == set(review_mod.ADOPTABLE)
    assert set(review_mod.NOT_ADOPTABLE_REASON) == set(whatif_tool.LEVERS) - review_mod.ADOPTABLE
    assert isinstance(briefs["DB-31"], LeverBrief) and briefs["DB-30"].choices == ["A", "B"]


# --------------------------------------------------------------------------- runner, review off
def test_optimise_candidate_id_is_runner_only(tmp_path, monkeypatch):
    """The TL-13 pin is hidden from the agent's tool brief and an agent value is never used."""
    assert "candidate_id" not in stub_toolbox().brief("optimise_design").args
    assert "top_n" in stub_toolbox().brief("optimise_design").args
    assert "candidate_id" in stub_toolbox().brief("verify_design").args  # ordinary arguments stay

    rewritten = []

    def pins_c3(messages, info):
        if output_name(info) == "Decision":
            prompt = user_prompt(messages)
            if (
                '"phase":"P6"' in prompt
                and '"optimise_design"' in prompt.split("completed_tools")[0]
            ):
                decision = json.loads(policy_like(messages, info).parts[0].content)
                step = decision["steps"][0]
                if step.get("tool") == "optimise_design":
                    step["args"] = {"top_n": 3, "candidate_id": "C3-2d"}
                    rewritten.append(step)
                    return ModelResponse(parts=[TextPart(json.dumps(decision))])
        return policy_like(messages, info)

    engine, _ = _llm_engine(tmp_path, "pin", pins_c3)
    runner = DesignRunner(engine, runs_root=tmp_path)  # no --review
    result = runner.start("", run_id="pin")
    assert result.status == RunStatus.COMPLETED
    state = runner.load_state("pin")
    p6 = [r for r in state.tool_results if r.tool == "optimise_design"]
    assert rewritten and p6 and all(r.args.get("candidate_id") is None for r in p6)
    assert all(r.source == "llm" for r in p6)  # the agent's step ran, with the pin removed
    assert result.selected_id == "C1-3d"  # the ranking, not the agent, chose


def test_more_or_fewer_doublets_from_the_engine_are_stepped_by_the_runner(tmp_path, monkeypatch):
    """Rule 1: the LLM returns 'more'; the runner adds one to the selected design's count."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    question = _question("x")
    assert (
        check_lever_choice(question, LeverChoice(lever="doublets", value="more", rationale="r"))
        is None
    )
    engine, _ = _llm_engine(
        tmp_path, "mo", _review_answer("doublets", "fewer", "One doublet less.")
    )
    script = Script({"REVIEW-0-1": "fewer doublets", "REVIEW-ADOPT-0": "no"})
    runner = DesignRunner(engine, runs_root=tmp_path, review=True, ask_fn=script)
    result = runner.start("", run_id="mo", input_mode="ask")
    assert result.status == RunStatus.COMPLETED and calls == [{"doublets": 2}]  # C1-3d has 3
    mapped = next(e for e in _events(runner, "mo") if e.type == "REVIEW_MAPPED")
    assert mapped.payload["value"] == "fewer" and mapped.payload["source"] == "llm"
    assert _dr12(runner, "mo").title == "User review: fewer doublets → doublets=2"


def test_review_budget_is_stored_with_the_run(tmp_path, monkeypatch):
    """A paused review resumes with the budget the run started with, and the summary lists adopted levers."""
    calls: list = []
    seen = []

    def spy(run_dir, levers, budget="quick", *, config_dir=None):
        seen.append(budget)
        return fake_whatif(calls)(run_dir, levers, budget, config_dir=config_dir)

    monkeypatch.setattr(review_mod, "whatif", spy)
    first = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True, review_budget="smoke")
    first.start("", run_id="b", input_mode="pause")
    for answer in ("A", "55", "25"):
        first.resume("b", answer)
    assert first.load_state("b").review_budget == "smoke"
    second = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True)  # default quick
    second.resume("b", "return temperature 65")
    assert seen == ["smoke"]
    result = second.resume("b", "yes")
    assert result.pending.request_id == "REVIEW-1-1"
    second.resume("b", "")
    second.resume("b", "yes")
    summary = json.loads((tmp_path / "b" / "run_summary.json").read_text("utf-8"))
    assert summary["review_levers"] == {"DB-31": 65.0}


def test_review_off_leaves_the_run_and_its_events_unchanged(tmp_path, monkeypatch):
    """Default: the review code path never runs; no new event or record appears."""

    def never(*args, **kwargs):
        raise AssertionError("review must not run without --review")

    monkeypatch.setattr(DesignRunner, "_review", never)
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path)
    result = runner.start("", run_id="off")
    assert result.status == RunStatus.COMPLETED
    types = _types(runner, "off")
    assert not set(types) & set(REVIEW_EVENTS)
    start = next(e for e in _events(runner, "off") if e.type == "RUN_START")
    assert start.payload == {"engine": "policy", "input_mode": "assume"}
    assert REVIEW_DR_ID not in result.decision_records
    state = runner.load_state("off")
    assert not state.review_enabled and state.review is None and state.review_pin is None
    assert "review" not in json.dumps(state.summary_dict())
    brief = runner.toolbox.brief("optimise_design")
    assert list(brief.args) == ["top_n"]
    assert not (tmp_path / "off" / "decision_records" / "DR-12.json").exists()


def test_review_budget_is_checked():
    """A review budget outside the what-if budgets is a configuration error."""
    with pytest.raises(ValueError, match="review_budget"):
        DesignRunner(PolicyEngine(), review=True, review_budget="huge")


# --------------------------------------------------------------------------- runner, review on
def test_assume_mode_records_no_objection_as_dr12_kept(tmp_path, monkeypatch):
    """Non-interactive run with --review: Enter = none, DR-12 'kept', no what-if, no loop."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True)
    result = runner.start("", run_id="a1")
    assert result.status == RunStatus.COMPLETED and REVIEW_DR_ID in result.decision_records
    assert calls == []
    types = _types(runner, "a1")
    assert types.count("REVIEW_START") == 1 and "REVIEW_NONE" in types and "REVIEW_KEPT" in types
    assert "LOOP_BACK" not in types
    record = _dr12(runner, "a1")
    assert record.choice == review_mod.KEEP_OPTION and record.decision_source == "user"
    assert "no objection" in record.rationale and record.criterion.key == "verdict"
    assert record.evidence_ids  # the P9 verification of the recommended design
    assert (tmp_path / "a1" / "decision_records" / "DR-12.md").exists()
    assert runner.load_state("a1").review is None


def test_policy_maps_boiler_too_big_and_adopting_reruns_p6_to_p9(tmp_path, monkeypatch):
    """'the boiler is too big' → OPT-ROBUST-BACKUP off → what-if → adopt → P6–P9 on the design.

    The rerun is the verifier loop-back path (loop 1 of 3); TL-13 (stub) is pinned to the adopted
    design, DR-12 records the adoption with the what-if evidence, DR-10 selects the design.
    """
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    script = Script({"REVIEW-0-1": "the boiler is too big", "REVIEW-ADOPT-0": "yes"})
    runner = DesignRunner(
        PolicyEngine(), runs_root=tmp_path, review=True, ask_fn=script, review_budget="smoke"
    )
    result = runner.start("", run_id="ad", input_mode="ask")
    assert result.status == RunStatus.COMPLETED, result.summary
    assert calls == [{"OPT-ROBUST-BACKUP": False}]
    state = runner.load_state("ad")
    adopted = "C1-3d-w1"
    assert result.selected_id == adopted and state.loop_count == 1
    assert state.review_pin == adopted and state.review_levers == {"OPT-ROBUST-BACKUP": False}
    events = _events(runner, "ad")
    types = [e.type for e in events]
    assert types.count("PHASE_START") == 11 + 4  # P0–P9, P6–P9 again, then P10 once
    mapped = next(e for e in events if e.type == "REVIEW_MAPPED")
    assert mapped.payload["lever"] == "OPT-ROBUST-BACKUP" and mapped.payload["value"] == "off"
    assert mapped.payload["source"] == "policy"
    loop = next(e for e in events if e.type == "LOOP_BACK")
    assert loop.payload["reason"] == "review" and loop.payload["loop"] == 1
    selected = [e.payload for e in events if e.type == "DESIGN_SELECTED"]
    assert selected[0]["candidate_id"] == adopted and selected[0]["dr_id"] == "DR-12"
    assert selected[0]["replaces"] == "C1-3d"
    # The adopted candidate joined the run with the what-if KPIs; TL-13 was pinned to it.
    assert adopted in {c.id for c in state.candidates} and state.kpis[adopted].lcoh_per_mwh == 40.0
    p6 = [r for r in state.tool_results if r.tool == "optimise_design" and r.loop == 1]
    assert p6 and all(r.args.get("candidate_id") == adopted for r in p6)
    assert state.shortlist[0] == adopted
    verified = [v.candidate_id for v in state.verifications if v.phase == Phase.P9]
    assert verified == ["C1-3d", adopted]
    # The what-if is evidence in the run; DR-12 of the second pass keeps the adoption history.
    whatif_records = [r for r in state.tool_results if r.tool == review_mod.WHATIF_TOOL]
    assert len(whatif_records) == 1 and whatif_records[0].source == "user"
    assert "lcoh_per_mwh" in whatif_records[0].data_keys
    adopted_event = next(e for e in events if e.type == "REVIEW_ADOPTED")
    assert adopted_event.payload["candidate_id"] == adopted
    dr12_events = [
        e for e in events if e.type == "DECISION_RECORD" and e.payload["dr_id"] == "DR-12"
    ]
    assert [e.payload["choice"] for e in dr12_events] == [adopted, review_mod.KEEP_OPTION]
    record = _dr12(runner, "ad")
    assert "levers adopted earlier in this run: OPT-ROBUST-BACKUP=off" in record.rationale
    dr10 = next(r for r in state.decision_records if r.dr_id == "DR-10")
    assert dr10.choice == adopted
    # The second pass asked again (no objection), then the approval.
    assert script.ids("REVIEW") == ["REVIEW-0-1", "REVIEW-ADOPT-0", "REVIEW-1-1"]
    assert "APPROVAL" in script.ids()
    adopt_question = dict(script.asked)["REVIEW-ADOPT-0"]
    assert "| LCOH | USD/MWh | 45.00 | 40.00 | -5.00 |" in adopt_question
    assert "Adopt the change? yes" in adopt_question and "guard APPROVED" in adopt_question
    # Adopted-lever constraint: the DR-11 robust option is not eligible in the rerun pass.
    dr11 = next(r for r in state.decision_records if r.dr_id == "DR-11")
    assert dr11.choice == "installed-backup"
    md = (tmp_path / "ad" / "decision_records" / "DR-12.md").read_text("utf-8")
    assert md.startswith("# DR-12 User review: ")
    assert (tmp_path / "ad" / "whatif" / "1" / "whatif.json").exists()


def test_adopted_dr12_record_names_the_lever_and_the_evidence(tmp_path, monkeypatch):
    """The DR-12 written at adoption: 'User review: <objection> → <lever>', evidence keys cited."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    captured = {}
    original = DesignRunner._finish_review

    def capture(self, state, result, **kwargs):
        outcome = original(self, state, result, **kwargs)
        if kwargs.get("adopted"):
            captured["record"] = next(r for r in state.decision_records if r.dr_id == "DR-12")
        return outcome

    monkeypatch.setattr(DesignRunner, "_finish_review", capture)
    script = Script({"REVIEW-0-1": "the boiler is too big", "REVIEW-ADOPT-0": "yes"})
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True, ask_fn=script)
    runner.start("", run_id="rec", input_mode="ask")
    record = captured["record"]
    assert record.title == "User review: the boiler is too big → OPT-ROBUST-BACKUP=off"
    assert record.choice == "C1-3d-w1" and record.phase == Phase.P9
    assert record.role.value == "lead_engineer" and record.decision_source == "user"
    assert record.criterion.key == "OPT-ROBUST-BACKUP" and record.criterion.source == "whatif"
    assert record.criterion.best_option_ids == ["C1-3d-w1"]
    assert {o.id for o in record.options} == {"kept", "C1-3d-w1"}
    kept = next(o for o in record.options if o.id == "kept")
    assert kept.label == "Keep the recommended design C1-3d" and "UNVERIFIED" in kept.notes
    assert any(e.startswith("TR-") for e in record.evidence_ids) and len(record.evidence_ids) == 2
    assert "simulate_annual" in record.rationale and "adopted" in record.rationale


def test_unmatched_objection_asks_again_three_times_then_keeps(tmp_path, monkeypatch):
    """'no lever matches' → say so and ask again (max 3 rounds), then DR-12 kept, no what-if."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    script = Script({f"REVIEW-0-{i}": "make it prettier" for i in (1, 2, 3, 4)})
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True, ask_fn=script)
    result = runner.start("", run_id="um", input_mode="ask")
    assert result.status == RunStatus.COMPLETED and calls == []
    assert script.ids("REVIEW") == ["REVIEW-0-1", "REVIEW-0-2", "REVIEW-0-3"]
    second = dict(script.asked)["REVIEW-0-2"]
    assert second.startswith("No lever matches 'make it prettier'")
    types = _types(runner, "um")
    assert types.count("REVIEW_UNMATCHED") == 3 and "LOOP_BACK" not in types
    record = _dr12(runner, "um")
    assert record.choice == "kept" and "no lever matched in 3 rounds" in record.rationale
    assert runner.load_state("um").loop_count == 0


def test_out_of_bound_lever_is_refused_with_the_guard_reason_and_asked_again(tmp_path, monkeypatch):
    """'5 doublets' maps, the what-if refuses it (DB-10) and the user is asked again."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    script = Script({"REVIEW-0-1": "I'd prefer 5 doublets"})
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True, ask_fn=script)
    result = runner.start("", run_id="rf", input_mode="ask")
    assert result.status == RunStatus.COMPLETED and calls == [{"doublets": 5}]
    refused = next(e for e in _events(runner, "rf") if e.type == "REVIEW_REFUSED")
    assert refused.payload["message"].startswith("doublets=5 refused: DesignGuard")
    assert "DB-10" in dict(script.asked)["REVIEW-0-2"]
    assert _dr12(runner, "rf").choice == "kept"
    assert not (tmp_path / "rf" / "whatif").exists()


def test_declining_records_dr12_kept_with_the_whatif_evidence(tmp_path, monkeypatch):
    """adopt? no → DR-12 kept, the what-if stays as evidence, no loop-back."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    script = Script({"REVIEW-0-1": "return temperature 65", "REVIEW-ADOPT-0": "no"})
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True, ask_fn=script)
    result = runner.start("", run_id="dc", input_mode="ask")
    assert result.status == RunStatus.COMPLETED and result.selected_id == "C1-3d"
    assert calls == [{"DB-31": 65.0}]
    state = runner.load_state("dc")
    assert state.loop_count == 0 and state.review_pin is None and not state.review_levers
    record = _dr12(runner, "dc")
    assert record.choice == "kept" and "declined by the user" in record.rationale
    assert record.title == "User review: return temperature 65 → DB-31=65"
    whatif_tr = next(r for r in state.tool_results if r.tool == "whatif")
    assert whatif_tr.id in record.evidence_ids
    adopt_option = next(o for o in record.options if o.id == "C1-3d-w1")
    assert adopt_option.eligible and adopt_option.recommended  # lower fabricated LCOH
    assert "LOOP_BACK" not in _types(runner, "dc")
    # DB-31 was not adopted: the run's answer is unchanged.
    assert state.answer_values()["DB-31"] == 55.0


def test_not_adoptable_lever_is_shown_but_kept(tmp_path, monkeypatch):
    """A flow objection maps to the TL-13 size variable: the what-if is shown, 'yes' is kept."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    script = Script({"REVIEW-0-1": "flow 200 m3/h", "REVIEW-ADOPT-0": "yes"})
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True, ask_fn=script)
    result = runner.start("", run_id="na", input_mode="ask")
    assert result.status == RunStatus.COMPLETED and calls == [{"design_flow_m3h": 200.0}]
    assert "cannot be adopted in a run" in dict(script.asked)["REVIEW-ADOPT-0"]
    record = _dr12(runner, "na")
    assert record.choice == "kept" and "cannot be adopted" in record.rationale
    option = next(o for o in record.options if o.id != "kept")
    assert not option.eligible and "not adoptable" in option.notes
    assert runner.load_state("na").loop_count == 0


def test_adopting_a_return_temperature_records_the_user_answer(tmp_path, monkeypatch):
    """DB-31 adopted: the rerun reads it as a user answer (TL-13, DR-08 options)."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    script = Script({"REVIEW-0-1": "return temperature 65", "REVIEW-ADOPT-0": "yes"})
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True, ask_fn=script)
    result = runner.start("", run_id="rt", input_mode="ask")
    assert result.status == RunStatus.COMPLETED and result.selected_id == "C1-3d-w1"
    state = runner.load_state("rt")
    assert state.answer_values()["DB-31"] == 65.0
    answer = next(a for a in reversed(state.answers) if a.assumption_id == "DB-31")
    assert answer.source == "user" and answer.request_id.startswith("REQ-")
    adopted = next(c for c in state.candidates if c.id == "C1-3d-w1")
    assert adopted.heating_return_degc == 65.0
    assert state.violations and "DB-31=65" in state.violations[0]


def test_loop_limit_blocks_a_fourth_change(tmp_path, monkeypatch):
    """Adoption counts against the ≤ 3 loops: at the limit the change is kept, not escalated."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    answers = {f"REVIEW-{i}-1": "return temperature 65" for i in range(5)}
    answers |= {f"REVIEW-ADOPT-{i}": "yes" for i in range(5)}
    script = Script(answers)
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True, ask_fn=script)
    result = runner.start("", run_id="lim", input_mode="ask")
    assert result.status == RunStatus.COMPLETED, result.summary
    state = runner.load_state("lim")
    assert state.loop_count == 3 and len(calls) == 4
    assert result.selected_id == "C1-3d-w1-w2-w3"
    record = _dr12(runner, "lim")
    assert record.choice == "kept" and "verification loops are used" in record.rationale
    assert _types(runner, "lim").count("LOOP_BACK") == 3


# --------------------------------------------------------------------------- LLM engine
def _review_answer(lever, value, rationale):
    def func(messages, info):
        if output_name(info) == "LeverChoice":
            choice = {"lever": lever, "value": value, "rationale": rationale}
            return ModelResponse(parts=[TextPart(json.dumps(choice))])
        return policy_like(messages, info)

    return func


def test_function_model_maps_boiler_too_big_to_robust_backup_off(tmp_path, monkeypatch):
    """--engine llm: the Lead Engineer's NativeOutput call maps the objection; logged like any call."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    seen = {}

    def func(messages, info):
        if output_name(info) == "LeverChoice":
            seen["prompt"] = user_prompt(messages)
        return _review_answer(
            "OPT-ROBUST-BACKUP",
            "off",
            "The objection is about the backup boiler size; OPT-ROBUST-BACKUP off removes the "
            "robust nameplates.",
        )(messages, info)

    engine, logger = _llm_engine(tmp_path, "llm", func)
    script = Script({"REVIEW-0-1": "boiler too big", "REVIEW-ADOPT-0": "no"})
    runner = DesignRunner(engine, runs_root=tmp_path, review=True, ask_fn=script)
    result = runner.start("", run_id="llm", input_mode="ask")
    assert result.status == RunStatus.COMPLETED and calls == [{"OPT-ROBUST-BACKUP": False}]
    mapped = next(e for e in _events(runner, "llm") if e.type == "REVIEW_MAPPED")
    assert mapped.payload["source"] == "llm" and mapped.payload["lever"] == "OPT-ROBUST-BACKUP"
    assert 'objects to the recommended design C1-3d: "boiler too big"' in seen["prompt"]
    assert '"adoptable":true' in seen["prompt"] and '"id":"OPT-ROBUST-BACKUP"' in seen["prompt"]
    p9 = [r for r in logger.records() if r.agent == "lead_engineer" and r.phase == "P9"]
    records = [r for r in p9 if isinstance(r.response, dict) and "lever" in r.response]
    assert len(records) == 1 and records[0].decision_source == "llm"  # DR-10 is the other P9 call
    assert records[0].response["lever"] == "OPT-ROBUST-BACKUP"
    state = runner.load_state("llm")
    assert state.fallback_count() == 0
    assert _dr12(runner, "llm").choice == "kept"


def test_invalid_llm_mapping_retries_then_falls_back_to_the_keyword_policy(tmp_path, monkeypatch):
    """An unknown lever from the LLM is retried (validator) and then mapped by the policy."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    engine, logger = _llm_engine(
        tmp_path, "fb", _review_answer("DB-99", "1", "An invented lever id.")
    )
    script = Script({"REVIEW-0-1": "the boiler is too big", "REVIEW-ADOPT-0": "no"})
    runner = DesignRunner(engine, runs_root=tmp_path, review=True, ask_fn=script)
    result = runner.start("", run_id="fb", input_mode="ask")
    assert result.status == RunStatus.COMPLETED and calls == [{"OPT-ROBUST-BACKUP": False}]
    mapped = next(e for e in _events(runner, "fb") if e.type == "REVIEW_MAPPED")
    assert mapped.payload["source"] == "policy_fallback"
    assert "offered ids" in (mapped.payload["error"] or "")
    record = next(
        r
        for r in logger.records()
        if r.agent == "lead_engineer" and r.phase == "P9" and r.decision_source != "llm"
    )
    assert record.decision_source == "policy_fallback" and record.retries == 2
    state = runner.load_state("fb")
    assert state.progress["P9"].fallbacks == 1 and state.fallback_count() >= 1


def test_llm_no_match_asks_again(tmp_path, monkeypatch):
    """lever null from the LLM = no lever matches: the user is asked again with the reason."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    engine, _ = _llm_engine(
        tmp_path, "nm", _review_answer(None, None, "No design lever expresses the objection.")
    )
    script = Script({"REVIEW-0-1": "make it prettier", "REVIEW-0-2": ""})
    runner = DesignRunner(engine, runs_root=tmp_path, review=True, ask_fn=script)
    result = runner.start("", run_id="nm", input_mode="ask")
    assert result.status == RunStatus.COMPLETED and calls == []
    assert "No design lever expresses the objection" in dict(script.asked)["REVIEW-0-2"]
    assert _dr12(runner, "nm").choice == "kept"


# --------------------------------------------------------------------------- pause / resume
def test_pause_mode_review_round_trip_across_runner_instances(tmp_path, monkeypatch):
    """pause mode: the objection and the adopt question are pending questions for ``resume``."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    first = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True)
    result = first.start("", run_id="p", input_mode="pause")
    assert result.pending.assumption_id == "DB-30"
    first.close()
    second = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True)
    second.resume("p", "A")
    second.resume("p", "55")
    result = second.resume("p", "25")
    assert result.status == RunStatus.PAUSED and result.pending.request_id == "REVIEW-0-1"
    assert result.pending.question.startswith("Any objection to the recommended design C1-3d")
    assert result.pending.default == "none"
    result = second.resume("p", "I'd prefer 3 doublets")
    assert result.status == RunStatus.PAUSED and result.pending.request_id == "REVIEW-ADOPT-0"
    assert "| LCOH | USD/MWh |" in result.pending.question and calls == [{"doublets": 3}]
    assert second.load_state("p").review.stage == "adopt"
    with pytest.raises(InvalidAnswer, match="yes or no"):
        second.resume("p", "maybe")
    third = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True)  # new process
    result = third.resume("p", "no")
    assert result.status == RunStatus.PAUSED and result.pending.request_id == "APPROVAL"
    result = third.resume("p", "yes")
    assert result.status == RunStatus.COMPLETED and result.selected_id == "C1-3d"
    events = _events(third, "p")
    assert [e.type for e in events].count("VERIFY_GATE") == 2  # P7 and P9 once each
    record = _dr12(third, "p")
    assert record.choice == "kept" and "doublets=3" in record.title


def test_pause_mode_enter_means_no_objection(tmp_path, monkeypatch):
    """``resume`` with an empty answer on the objection question = none (Enter)."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path, review=True)
    runner.start("design the geothermal plant", run_id="e", input_mode="pause")
    for answer in ("defaults", "A", "55", "25"):
        result = runner.resume("e", answer)
    assert result.pending.request_id == "REVIEW-0-1"
    result = runner.resume("e", "")
    assert result.pending.request_id == "APPROVAL" and calls == []
    assert _dr12(runner, "e").choice == "kept"


# --------------------------------------------------------------------------- CLI
def test_cli_run_review_flag_reaches_the_runner(tmp_path, capsys, monkeypatch):
    """`run --review` (assume mode offline): DR-12 is written; without the flag it is not."""
    monkeypatch.setattr(review_mod, "whatif", fake_whatif([]))
    base = ["run", "--engine", "policy", "--runs-dir", str(tmp_path), "--input-mode", "assume"]
    assert main([*base, "--run-id", "with", "--review"]) == EXIT_OK
    assert (tmp_path / "with" / "decision_records" / "DR-12.json").exists()
    assert main([*base, "--run-id", "without"]) == EXIT_OK
    assert not (tmp_path / "without" / "decision_records" / "DR-12.json").exists()
    out = capsys.readouterr().out
    assert "run with: completed" in out and "run without: completed" in out


# --------------------------------------------------------------------------- real tools (slow)
def test_real_tools_review_adopts_a_return_temperature_and_tl13_sizes_from_it(tmp_path_factory):
    """End to end on the real tools (smoke budget): objection → what-if → adopt → P6–P9 rerun.

    TL-13 is pinned to the adopted what-if design (its doublets, depth and the adopted DB-31),
    the re-sized design is verified ACCEPTED, DR-12 then records the second review as kept.
    """
    root = tmp_path_factory.mktemp("runs")
    script = Script({"REVIEW-0-1": "return temperature 50", "REVIEW-ADOPT-0": "yes"})
    runner = DesignRunner(
        PolicyEngine(),
        runs_root=root,
        toolbox=real_toolbox("smoke"),
        review=True,
        review_budget="smoke",
        ask_fn=script,
    )
    try:
        result = runner.start("", run_id="real", input_mode="ask")
    finally:
        runner.close()
    assert result.status == RunStatus.COMPLETED, result.summary
    state = runner.load_state("real")
    assert state.loop_count == 1 and state.review_levers == {"DB-31": 50.0}
    assert state.review_pin and state.review_pin.endswith("-w1")
    assert result.selected_id == f"{state.review_pin}-opt"
    assert result.verdict == DesignVerdict.ACCEPTED.value
    selected = next(c for c in state.candidates if c.id == result.selected_id)
    pinned = next(c for c in state.candidates if c.id == state.review_pin)
    assert selected.heating_return_degc == 50.0 == pinned.heating_return_degc
    assert (selected.doublets, selected.depth_m) == (pinned.doublets, pinned.depth_m)
    assert "sized from the adopted design" in selected.description
    sized = json.loads((root / "real" / SESSION_DIR / "sized_designs.json").read_text("utf-8"))
    assert state.review_pin in sized and result.selected_id in sized
    assert state.answer_values()["DB-31"] == 50.0
    p6 = [r for r in state.tool_results if r.tool == "optimise_design" and r.loop == 1]
    assert p6 and p6[0].args["candidate_id"] == state.review_pin and p6[0].ok
    whatif_dir = root / "real" / "whatif" / "1"
    assert (whatif_dir / "whatif.json").exists() and (whatif_dir / "DR-W1.json").exists()
    record = _dr12(runner, "real")
    assert record.choice == "kept" and "levers adopted earlier in this run: DB-31=50" in (
        record.rationale
    )
    assert script.ids("REVIEW") == ["REVIEW-0-1", "REVIEW-ADOPT-0", "REVIEW-1-1"]
    assert not any(r.stub for r in state.tool_results)
