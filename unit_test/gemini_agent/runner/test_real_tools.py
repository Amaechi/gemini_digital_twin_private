"""Tests of the WP-14 real tool bindings (runner/real_tools.py) and the TL-21 results pack.

One module-scoped smoke run (profile ``smoke``: 13 sampled days, minimal DE / Monte Carlo) drives
the policy workflow P0–P10 on the real tools (≈ 1 min); the other tests are fast adapter checks.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gemini_agent.agents.engine import PolicyEngine
from gemini_agent.memory.project_knowledge import KNOWLEDGE_FILE, ProjectFact
from gemini_agent.runner import real_tools
from gemini_agent.runner.design_runner import DesignRunner
from gemini_agent.runner.real_tools import (
    InterferenceArgs,
    SizedDesign,
    ToolSession,
    make_profile,
    real_toolbox,
)
from gemini_agent.runner.tool_stubs import stub_toolbox
from gemini_agent.runner.toolbox import (
    CandidateArgs,
    NoArgs,
    ToolContext,
    default_toolbox,
)
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import GasHandling
from gemini_agent.schemas.decision import DesignVerdict, RunStatus
from gemini_agent.schemas.results import KPISet
from gemini_agent.tools import plots
from gemini_agent.verify.design_verifier import VERIFIER_NAME, VerifyDesignTool


def _context(tmp_path: Path, **kwargs) -> ToolContext:
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True, exist_ok=True)
    return ToolContext(run_id="r1", run_dir=run_dir, config_dir=None, **kwargs)


def _candidate(**changes) -> Candidate:
    data = {
        "id": "C2-2d",
        "concept": "C2",
        "doublets": 2,
        "depth_m": 2500.0,
        "design_flow_m3h": 300.0,
        "gas_handling": GasHandling.DEGAS_USE,
        "heating_return_degc": 55.0,
        "cooling_return_degc": 26.0,
    }
    data.update(changes)
    return Candidate(**data)


# --------------------------------------------------------------------------- catalogue
def test_default_toolbox_binds_every_catalogue_name_to_a_real_tool():
    """Same names as the stub catalogue, none a stub; verify_design uses the session collector."""
    box = default_toolbox()
    assert set(stub_toolbox().names()) <= set(box.names())
    assert [name for name in box.names() if box.spec(name).stub] == []
    verifier = box.spec("verify_design").func
    assert isinstance(verifier, VerifyDesignTool)
    assert verifier.evidence_provider.__self__ is box.session  # type: ignore[attr-defined]


def test_budgets_come_from_the_register(monkeypatch):
    """RUN-BUDGET: full keeps OPT-DE/UQ-MC (None); smoke = 1 concept, 13 days, no pool; the
    default budget is GEOAGENT_BUDGET (tests run with smoke), else full."""
    full = make_profile("full", workers=2)
    assert (full.popsize, full.maxiter, full.mc_samples, full.day_indices) == (None,) * 4
    assert full.workers == 2 and full.max_concepts is None
    smoke = make_profile("smoke")
    assert smoke.workers == 1 and smoke.max_concepts == 1 and len(smoke.day_indices) == 13
    assert (smoke.maxiter, smoke.mc_samples, smoke.uq_cases) == (0, 2, False)
    assert make_profile().name == "smoke"  # conftest sets the budget for every test
    monkeypatch.delenv(real_tools.BUDGET_ENV)
    assert make_profile().name == "full"
    with pytest.raises(ValueError, match="unknown tool profile"):
        make_profile("fastest")


# --------------------------------------------------------------------------- adapters
def test_load_design_basis_lists_gaps_and_remembered_answers(tmp_path):
    """DB-30…32 are open; a durable user answer of an earlier run is named, not assumed."""
    context = _context(tmp_path)
    store_path = context.run_dir.parent / KNOWLEDGE_FILE
    box = real_toolbox("smoke")
    box.session.knowledge(context).save(
        ProjectFact(
            knowledge_id="k1",
            assertion="DB-31 = 60 degC (user answer)",
            assumption_id="DB-31",
            value=60.0,
            source="user_answer",
            learned_at="2026-11-02T00:00:00Z",
        )
    )
    assert store_path.is_file()
    result = box.execute("load_design_basis", {}, context)
    assert result.data.open_gaps == ["DB-30", "DB-31", "DB-32"]
    assert result.data.remembered_answers == {"DB-31": 60.0}
    assert "earlier user answer: 60.0" in result.data.gap_questions["DB-31"]
    assert result.data.report.derived is not None  # DB-20…29 now computed (WP-04)


def test_pressure_interference_defaults_to_the_db10_maximum(tmp_path):
    """TL-08 argument model: no doublet count → 3 doublets (DB-10 worst case)."""
    box = real_toolbox("smoke")
    assert InterferenceArgs().doublets is None
    result = box.execute("pressure_interference", {}, _context(tmp_path))
    assert result.data.doublets == 3


def test_design_guard_reports_failed_ids_as_checks(tmp_path):
    """TL-10 results → reducer ``checks``; a 4-doublet candidate fails DB-10."""
    good, bad = _candidate(), _candidate(id="C1-4d", concept="C1", doublets=4)
    context = _context(tmp_path, candidates=(good, bad))
    result = real_toolbox("smoke").execute("design_guard", {"candidate_id": "all"}, context)
    checks = {c.candidate_id: c for c in result.data.checks}
    assert checks["C2-2d"].passed and not checks["C1-4d"].passed
    assert any("DB-10" in v or "doublet" in v for v in checks["C1-4d"].violations)


def test_guard_excludes_unverifiable_depths_but_keeps_them_for_screening(tmp_path):
    """P3 verifier pre-check: C4 at 3.2 km fails (PH-19/PH-30), is excluded from ranking and the
    DRs (``guard_failed``) yet stays in the P5 screening for the comparison table."""
    c2, c4 = _candidate(), _candidate(id="C4-2d", concept="C4", depth_m=3200.0)
    box = real_toolbox("smoke")
    context = _context(tmp_path, candidates=(c2, c4))
    checks = {c.candidate_id: c for c in box.execute("design_guard", {}, context).data.checks}
    assert checks["C2-2d"].passed and not checks["C4-2d"].passed
    assert "verifier pre-check: depth 3200 m" in checks["C4-2d"].violations[-1]
    assert "PH-19" in checks["C4-2d"].violations[-1]
    later = _context(tmp_path, candidates=(c2, c4), guard_failed=("C4-2d",))
    assert [c.id for c in box.session.select(later, "all")] == ["C2-2d", "C4-2d"]
    assert "C4-2d" in later.excluded_ids


def test_depth_check_keeps_only_depths_the_verifier_can_accept(tmp_path):
    """TL-05 at 300 m³/h: T_res 87 °C at 2.5 km (kept); 3.0 km has brine above 100 °C at the ESP
    (NPSH not evaluable, PH-19); 3.2 km also flashes at the letdown (T_wh 105 °C, PH-30)."""
    session = ToolSession("smoke")
    context = _context(tmp_path)
    assert session.depth_check(context, 2500.0, degas=True) == ""
    assert "PH-19" in session.depth_check(context, 3000.0, degas=False)
    assert session.letdown_safe(context, 3000.0) and not session.letdown_safe(context, 3200.0)


def test_sized_designs_survive_a_new_session(tmp_path):
    """A resumed run (new process) recovers P6's sized candidates and outage schedules."""
    context = _context(tmp_path)
    first = ToolSession("smoke")
    memo = first.memo(context)
    sized = _candidate(id="C2-opt", depth_m=3000.0)
    memo.sized["C2-opt"] = SizedDesign(
        candidate=sized, concept_candidate_id="C2-2d", outage_start_indices=[150, 240]
    )
    first._save_sized(memo)
    second = ToolSession("smoke")
    assert second.candidate(context, "C2-opt") == sized
    assert second.outages(context, "C2-opt") == [150, 240]


def test_unknown_candidate_is_an_error(tmp_path):
    """Adapters refuse ids that are neither in the run nor sized."""
    box = real_toolbox("smoke")
    with pytest.raises(ValueError, match="unknown candidate_id"):
        box.execute("co2", CandidateArgs(candidate_id="C9-9d").model_dump(), _context(tmp_path))
    assert NoArgs.model_validate({}) is not None


class _NoOptimum:
    """Stand-in TL-13 result without a design that meets demand."""

    best = None
    screening = ()

    def model_dump(self, **kwargs):
        return {}


def test_optimise_skips_rejected_unverifiable_and_failed_concepts(tmp_path, monkeypatch):
    """P6: a REJECTED sized design is not re-proposed; quick profile sizes the own point or the
    deepest verifiable register depth above it (C4 3.2 km → 2.5 km); no optimum → skipped."""
    requests = []

    def fake_tl13(request, **kwargs):
        requests.append(request)
        return SimpleNamespace(data=_NoOptimum())

    monkeypatch.setattr(real_tools, "tl13_optimise_design", fake_tl13)
    c1 = _candidate(id="C1-3d", concept="C1", doublets=3)
    c4 = _candidate(id="C4-2d", concept="C4", depth_m=3200.0)
    kpis = {
        cid: KPISet(candidate_id=cid, lcoh_per_mwh=value)
        for cid, value in (("C1-3d", 30.0), ("C2-2d", 40.0), ("C4-2d", 50.0))
    }
    context = _context(
        tmp_path, candidates=(c1, _candidate(), c4), kpis=kpis, rejected_ids=("C1-opt",)
    )
    result = real_toolbox("quick").execute("optimise_design", {"top_n": 3}, context)
    assert result.data.shortlist == [] and result.data.candidates == []
    assert "rejected by the verifier" in result.data.skipped["C1-3d"]
    assert "no design that meets demand" in result.data.skipped["C2-2d"]
    assert [r.concept for r in requests] == ["C2", "C4"]
    assert [r.depth_options_m for r in requests] == [[2500.0], [2500.0]]
    assert [r.doublet_options for r in requests] == [[2], [2]]


# --------------------------------------------------------------------------- end to end
@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory):
    """Policy workflow P0–P10 on the real tools (smoke profile), once for the module."""
    root = tmp_path_factory.mktemp("runs")
    runner = DesignRunner(PolicyEngine(), runs_root=root, toolbox=real_toolbox("smoke"))
    try:
        result = runner.start("", run_id="smoke")
        state = runner.load_state("smoke")
    finally:
        runner.close()
    return result, state, root / "smoke"


def test_policy_pipeline_on_real_tools_is_accepted_without_stubs(smoke_run):
    """S1 shape: completed, ACCEPTED by the real DesignVerifier, no stub call, DR-01…DR-11."""
    result, state, run_dir = smoke_run
    assert result.status == RunStatus.COMPLETED, result.summary
    assert result.verdict == DesignVerdict.ACCEPTED.value
    assert not any(record.stub for record in state.tool_results)
    latest = state.latest_verification(state.selected_id)
    assert latest is not None and latest.verifier == VERIFIER_NAME
    assert {r.dr_id for r in state.decision_records} == {f"DR-{i:02d}" for i in range(1, 12)}
    # P8 workflow step (pre-L-2): the TL-15 robust backups priced, DR-11 decides the installation.
    backup = state.robust_backup
    step = next(r for r in state.tool_results if r.tool == "robust_backup")
    assert (step.phase.value, step.source, step.id) == ("P8", "workflow", backup.tool_result_id)
    dr11 = next(r for r in state.decision_records if r.dr_id == "DR-11")
    analysed = backup.candidate_id
    assert analysed.endswith("-opt")  # P6's sized design joined the run and TL-15 analysed it
    assert [c.id for c in state.candidates if c.id.endswith("-opt")] == [analysed]
    assert state.selected_id == (
        analysed if dr11.choice == "installed-backup" else f"{analysed}-robust"
    )
    assert state.selected_id in {c.id for c in state.candidates}
    assert state.analysed_id(state.selected_id) == analysed
    if backup.robust_candidate_id is not None:  # the variant is priced on the same basis
        robust = state.kpis[backup.robust_candidate_id]
        assert backup.year_mode == "full" and robust.lcoh_per_mwh is not None
        assert backup.delta_lcoh_per_mwh == pytest.approx(
            robust.lcoh_per_mwh - state.kpis[analysed].lcoh_per_mwh
        )
        assert backup.delta_capex_million > 0  # the added nameplate is priced
    kpis = state.kpis[analysed]
    assert kpis.lcoh_per_mwh is not None and kpis.co2_reduction_vs_c0_frac is not None
    p7 = [r for r in state.tool_results if r.tool == "simulate_annual" and r.phase.value == "P7"]
    full = json.loads((run_dir / p7[-1].path).read_text("utf-8"))["data"]["kpis"]
    full_kpis = next(k for k in full if k["candidate_id"] == analysed)
    assert kpis.lcoh_per_mwh == full_kpis["lcoh_per_mwh"]  # P8 adds percentiles only
    assert kpis.unmet_heating_days == full_kpis["unmet_heating_days"]
    assert kpis.exergy_efficiency_frac is not None
    assert {"p10", "p50", "p90"} <= set(kpis.percentiles["lcoh_per_mwh"])
    for name in ("report_draft.md", "figures/fig_daily_heat.png", "session/sized_designs.json"):
        assert (run_dir / name).is_file(), name


S1_FIXTURE = Path(__file__).parent / "fixtures" / "s1_policy_smoke.json"


def test_s1_policy_smoke_run_matches_the_recorded_fixture(smoke_run):
    """S1 regression (WP-13 follow-up): the P1 intake questions never touch the full brief.

    The fixture was recorded before CFG-BASIS-CONFIRM / CFG-INPUT-DEFAULTS existed: the same
    selected design, wells, equipment, KPIs and exactly DB-30/31/32 + approval as questions.
    """
    _, state, _ = smoke_run
    fixture = json.loads(S1_FIXTURE.read_text(encoding="utf-8"))
    selected = next(c for c in state.candidates if c.id == state.selected_id)
    assert state.status.value == fixture["status"]
    assert state.verdict_value() == fixture["verdict"]
    assert state.selected_id == fixture["selected_id"]
    assert (selected.doublets, selected.depth_m, selected.design_flow_m3h) == (
        fixture["doublets"],
        fixture["depth_m"],
        fixture["design_flow_m3h"],
    )
    assert selected.equipment_sizes == pytest.approx(fixture["equipment_sizes"])
    assert [a.label for a in state.answers] == fixture["answers"]
    kpis = state.kpis[state.analysed_id(state.selected_id)]
    assert kpis.lcoh_per_mwh == pytest.approx(fixture["lcoh_per_mwh"], rel=1e-6)
    assert kpis.capex_million == pytest.approx(fixture["capex_million"], rel=1e-6)
    assert state.missing_inputs == [] and state.conflicts == []


def test_results_pack_renders_from_the_finished_run(smoke_run, tmp_path):
    """TL-21 from the run's files alone: figures, tables, report with the selected design."""
    _, state, run_dir = smoke_run
    view = plots.RunView.from_run(run_dir)
    files, tables = plots.render_all(view, tmp_path / "results")
    assert {"fig_candidates.png", "fig_daily_heat.png", "fig_daily_cooling.png",
            "fig_load_duration.png", "fig_energy_balance.png", "fig_uncertainty.png",
            "fig_tornado.png", "fig_tokens.png"} <= set(files)  # fmt: skip
    assert "verification.md" in tables and "decision_records.md" in tables
    text, sections = plots.report_markdown(view)
    assert state.selected_id in text and "Answer" in sections
    assert "Verdict: ACCEPTED" in (tmp_path / "results" / "verification.md").read_text("utf-8")
    title, bars = plots.tornado_bars(view)
    assert bars and "USD/MWh" not in title
    rows = json.loads(json.dumps(plots.candidates_table(view)[1].to_dict("records")))
    assert any(row["concept"] == "C0" for row in rows)


def test_robust_backup_branches_never_escalate(smoke_run, tmp_path, monkeypatch):
    """Pre-L-2 step on the smoke run's files (resume path): variant, no TL-15, nothing to add,
    failed evaluation — the last three return a reason instead of raising (DR-11: installed)."""
    import shutil

    _, state, run_dir = smoke_run
    work = tmp_path / "runs" / "smoke"
    shutil.copytree(run_dir, work)
    analysed = state.robust_backup.candidate_id
    context = ToolContext(
        run_id="smoke",
        run_dir=work,
        config_dir=None,
        candidates=tuple(c for c in state.candidates if not c.id.endswith("-robust")),
        kpis=dict(state.kpis),
        selected_id=analysed,
    )
    session = ToolSession("smoke")
    tools = real_tools.RealTools(session)
    args = CandidateArgs(candidate_id=analysed)
    result = tools.robust_backup(args, context)  # TL-15 read back from uncertainty_<id>.json
    out = result.data
    assert out.robust_candidate_id == f"{analysed}-robust" and out.year_mode == "fast"
    assert out.added_backup_mw and out.delta_capex_million > 0
    assert len(result.summary) <= 300 and "DR-11 decides" in result.summary
    sized = json.loads((work / "session" / "sized_designs.json").read_text("utf-8"))
    assert sized[out.robust_candidate_id]["robust_backup_of"] == analysed

    other = next(c.id for c in context.candidates if c.id != analysed and c.doublets > 0)
    missing = tools.robust_backup(CandidateArgs(candidate_id=other), context).data
    assert missing.robust_candidate_id is None and "no TL-15 result" in missing.reason

    path = work / f"uncertainty_{analysed}.json"
    report = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps(report | {"robust_backup_mw": report["installed_backup_mw"]}))
    fresh = real_tools.RealTools(ToolSession("smoke"))
    nothing = fresh.robust_backup(args, context).data
    assert nothing.robust_candidate_id is None and "within the installed" in nothing.reason
    path.write_text(json.dumps(report))

    def broken(context, candidate):
        raise ValueError("no plant")

    failing = ToolSession("smoke")
    monkeypatch.setattr(failing, "screen", broken)
    failed = real_tools.RealTools(failing).robust_backup(args, context)
    assert failed.data.robust_candidate_id is None
    assert failed.data.reason == "variant not evaluated: no plant" and failed.warnings
    assert f"{analysed}-robust" not in failing.memo(context).sized
