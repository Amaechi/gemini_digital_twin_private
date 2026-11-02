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


def test_profiles_trade_effort_only():
    """``full`` keeps the register DE/MC settings (None); unknown names are refused."""
    full = make_profile("full", workers=2)
    assert (full.popsize, full.maxiter, full.mc_samples, full.day_indices) == (None,) * 4
    assert make_profile("smoke").day_indices is not None
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
    """S1 shape: completed, ACCEPTED by the real DesignVerifier, no stub call, DR-01…DR-10."""
    result, state, run_dir = smoke_run
    assert result.status == RunStatus.COMPLETED, result.summary
    assert result.verdict == DesignVerdict.ACCEPTED.value
    assert not any(record.stub for record in state.tool_results)
    latest = state.latest_verification(state.selected_id)
    assert latest is not None and latest.verifier == VERIFIER_NAME
    assert {r.dr_id for r in state.decision_records} == {f"DR-{i:02d}" for i in range(1, 11)}
    assert state.selected_id.endswith("-opt")  # P6's sized design joined the run
    assert state.selected_id in {c.id for c in state.candidates}
    kpis = state.kpis[state.selected_id]
    assert kpis.lcoh_per_mwh is not None and kpis.co2_reduction_vs_c0_frac is not None
    p7 = [r for r in state.tool_results if r.tool == "simulate_annual" and r.phase.value == "P7"]
    full = json.loads((run_dir / p7[-1].path).read_text("utf-8"))["data"]["kpis"]
    full_kpis = next(k for k in full if k["candidate_id"] == state.selected_id)
    assert kpis.lcoh_per_mwh == full_kpis["lcoh_per_mwh"]  # P8 adds percentiles only
    assert kpis.unmet_heating_days == full_kpis["unmet_heating_days"]
    assert kpis.exergy_efficiency_frac is not None
    assert {"p10", "p50", "p90"} <= set(kpis.percentiles["lcoh_per_mwh"])
    for name in ("report_draft.md", "figures/fig_daily_heat.png", "session/sized_designs.json"):
        assert (run_dir / name).is_file(), name


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
