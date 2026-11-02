"""Unit tests for gemini_agent.runner.decision_records (AG-07)."""

import json

import pytest

from gemini_agent.agents.engine import PolicyEngine, check_choice, choice_deps
from gemini_agent.runner.decision_records import (
    build_options,
    build_question,
    installed_design,
    make_record,
    result_keys,
    robust_backup_rule,
    write_records,
)
from gemini_agent.runner.state import RunState, ToolRecord, Verification
from gemini_agent.runner.tool_stubs import KPIBatch, stub_toolbox
from gemini_agent.runner.toolbox import ToolContext
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import DR_TITLES, DesignVerdict, OptionChoice, Phase
from gemini_agent.schemas.results import KPISet

pytestmark = pytest.mark.usefixtures("stub_tools")


def _state(tmp_path) -> RunState:
    toolbox = stub_toolbox()
    context = ToolContext(run_id="t", run_dir=tmp_path, config_dir=None)
    candidates = toolbox.execute("generate_candidates", {}, context).data.candidates
    state = RunState(run_id="t", user_prompt="design", engine="policy", input_mode="assume")
    state.candidates = list(candidates)
    state.tool_results.append(
        ToolRecord(
            id="TR-001",
            tool="simulate_annual",
            phase=Phase.P5,
            args={},
            ok=True,
            stub=False,
            candidate_ids=[c.id for c in candidates],
            source="policy",
        )
    )
    return state


def test_candidate_options_follow_the_kpi_ranking(tmp_path):
    """With KPIs, the option holding the lowest-LCOH candidate is recommended."""
    state = _state(tmp_path)
    state.kpis = {
        "C3-2d": KPISet(candidate_id="C3-2d", lcoh_per_mwh=40.0),
        "C1-3d": KPISet(candidate_id="C1-3d", lcoh_per_mwh=60.0),
    }
    for dr_id, expected in {
        "DR-01": "2-doublets",
        "DR-03": "hthp-series",
        "DR-04": "free-cooling-absorption",
        "DR-05": "no-storage",
        "DR-07": "degas-use",
        "DR-02": "depth-2500m",
    }.items():
        options = build_options(dr_id, state, None)
        assert [o.id for o in options if o.recommended] == [expected], dr_id
        assert all(o.evidence_ids for o in options)


def test_static_and_register_options(tmp_path):
    """DR-06/DR-09 encode DB-30 and spec §10.1; DR-08 lists DB-31 and its sensitivity values."""
    state = _state(tmp_path)
    assert [o.id for o in build_options("DR-06", state, None) if o.eligible] == [
        "optimiser-lowest-load"
    ]
    assert [o.id for o in build_options("DR-09", state, None) if o.eligible] == [
        "titanium-plates-no-cu"
    ]
    dr08 = build_options("DR-08", state, None)
    assert [o.id for o in dr08] == ["dh-return-55", "dh-return-45", "dh-return-65", "dh-return-75"]
    assert [o.id for o in dr08 if o.recommended] == ["dh-return-55"]


def test_final_selection_only_the_verified_design_is_eligible(tmp_path):
    """DR-10 offers the shortlist, but only the selected (verified) design can be chosen."""
    state = _state(tmp_path)
    assert build_question("DR-10", state, Phase.P9, None) is None  # nothing shortlisted yet
    state.shortlist = ["C2-2d", "C3-2d"]
    state.selected_id = "C2-2d"
    state.verifications.append(
        Verification(
            candidate_id="C2-2d",
            verdict=DesignVerdict.UNVERIFIED,
            verifier="stub",
            tool_result_id="TR-001",
            phase=Phase.P9,
        )
    )
    question = build_question("DR-10", state, Phase.P9, None)
    assert [(o.id, o.eligible) for o in question.options] == [("C2-2d", True), ("C3-2d", False)]
    choice = PolicyEngine().choose(question, None).value  # type: ignore[arg-type]
    record = make_record(question, choice, "policy", state)
    assert record.choice == "C2-2d" and not record.stub_evidence
    assert {r.option_id for r in record.rejected} == {"C3-2d"}


def test_final_selection_follows_the_latest_verdict(tmp_path):
    """DR-10: a REJECTED design is not eligible; once re-verified (same id) it is again."""
    state = _state(tmp_path)
    state.shortlist = ["C2-2d", "C3-2d"]
    state.selected_id = "C2-2d"

    def verify(verdict, phase):
        state.verifications.append(
            Verification(
                candidate_id="C2-2d",
                verdict=verdict,
                verifier="fake",
                tool_result_id="TR-001",
                phase=phase,
            )
        )

    verify(DesignVerdict.REJECTED, Phase.P9)
    assert build_question("DR-10", state, Phase.P9, None) is None  # skipped, gate loops back
    verify(DesignVerdict.ACCEPTED, Phase.P9)  # e.g. WP-11 resized the same candidate
    question = build_question("DR-10", state, Phase.P9, None)
    assert [(o.id, o.eligible) for o in question.options] == [("C2-2d", True), ("C3-2d", False)]
    state.guard_failed = ["C2-2d"]
    assert build_question("DR-10", state, Phase.P9, None) is None


def test_records_written_as_json_and_markdown(tmp_path):
    """Each DR becomes DR-xx.json + DR-xx.md, plus an index."""
    state = _state(tmp_path)
    question = build_question("DR-05", state, Phase.P5, None)
    record = make_record(question, PolicyEngine().choose(question, None).value, "policy", state)
    paths = write_records([record], tmp_path / "dr")
    assert sorted(p.name for p in paths) == ["DR-05.json", "DR-05.md", "index.md"]
    data = json.loads((tmp_path / "dr" / "DR-05.json").read_text())
    assert data["choice"] == record.choice and data["evidence_ids"] == ["TR-001"]
    markdown = (tmp_path / "dr" / "DR-05.md").read_text()
    assert "Rejected options" in markdown and record.choice in markdown


def test_dr01_quotes_the_productivity_basis(tmp_path):
    """WP-14 (S8): DR-01 options quote the permeability and the real TL-04 summary."""
    state = _state(tmp_path)
    state.tool_results.append(
        ToolRecord(
            id="TR-002",
            tool="productivity_index",
            phase=Phase.P2,
            args={},
            ok=True,
            summary="PI 3.05 m³/h/bar (k 50 mD, h 100 m)",
            stub=False,
            source="policy",
        )
    )
    notes = [option.notes for option in build_options("DR-01", state, None)]
    basis_k = load_config("design_basis").reservoir.permeability_md
    assert notes and all(
        f"permeability {basis_k:g} mD" in n and "TR-002 PI 3.05" in n for n in notes
    )
    state.tool_results[-1] = state.tool_results[-1].model_copy(update={"stub": True})
    assert not any("permeability" in o.notes for o in build_options("DR-01", state, None))


# --------------------------------------------------------------------------- L-1 follow-up
def _with_economics(state: RunState) -> RunState:
    """Add an economics record whose result holds KPISet fields (the citable keys)."""
    batch = KPIBatch(mode="economics", kpis=[KPISet(candidate_id="C1-3d")])
    state.tool_results.append(
        ToolRecord(
            id="TR-002",
            tool="economics",
            phase=Phase.P5,
            args={},
            ok=True,
            candidate_ids=[c.id for c in state.candidates],
            source="policy",
            data_keys=result_keys(batch),
        )
    )
    state.kpis = {
        "C3-2d": KPISet(candidate_id="C3-2d", lcoh_per_mwh=40.0, co2_t_per_yr=900.0),
        "C1-3d": KPISet(candidate_id="C1-3d", lcoh_per_mwh=60.0, co2_t_per_yr=500.0),
    }
    return state


def test_result_keys_walk_nested_models_not_equipment_names():
    """Evidence keys are field names (nested models too), not keys of nested mappings."""
    kpi = KPISet(candidate_id="C1-3d", heat_by_source_mwh={"hex": 1.0})
    keys = result_keys(KPIBatch(mode="economics", kpis=[kpi]))
    assert {"mode", "kpis", "lcoh_per_mwh", "heat_by_source_mwh"} <= set(keys)
    assert "hex" not in keys
    assert result_keys({"verdict": "UNVERIFIED", "nested": {"inner": 1}}) == ["verdict", "nested"]


def test_criteria_rank_rule_first_then_discriminating_kpis(tmp_path):
    """Fix 4: the recommendation rule is criterion one; KPI criteria only where options differ."""
    state = _with_economics(_state(tmp_path))
    question = build_question("DR-01", state, Phase.P5, None)
    assert {"economics", "simulate_annual", "lcoh_per_mwh", "co2_t_per_yr"} <= set(
        question.evidence_keys
    )
    by_id = {c.id: c for c in question.criteria}
    assert question.criteria[0].id == "ranking-lowest-lcoh_per_mwh"
    assert by_id["ranking-lowest-lcoh_per_mwh"].best_option_ids == ["2-doublets"]
    assert by_id["lowest-co2_t_per_yr"].best_option_ids == ["3-doublets"]
    assert by_id["lowest-co2_t_per_yr"].evidence_ids == ["TR-002"]
    assert "lowest-lcoh_per_mwh" not in by_id  # same key and winner as the rule: not repeated
    assert "lowest-unmet_heating_mwh" not in by_id  # no values: cannot decide

    policy = PolicyEngine().choose(question, None).value  # type: ignore[arg-type]
    assert (policy.criterion, policy.option_id) == ("ranking-lowest-lcoh_per_mwh", "2-doublets")
    assert check_choice(choice_deps(question), policy) is None

    # Another criterion may decide, if the choice follows it ...
    co2 = OptionChoice(
        dr_id="DR-01",
        criterion="lowest-co2_t_per_yr",
        option_id="3-doublets",
        rationale="lowest co2_t_per_yr among the eligible options in economics",
    )
    assert check_choice(choice_deps(question), co2) is None
    record = make_record(question, co2, "llm", state)
    assert record.criterion.key == "co2_t_per_yr"
    paths = write_records([record], tmp_path / "dr")
    markdown = next(p for p in paths if p.name == "DR-01.md").read_text()
    assert "Decisive criterion: lowest co2_t_per_yr" in markdown and "TR-002" in markdown
    assert "differs from the workflow-recommended option 2-doublets" in markdown
    # ... but not contradict it.
    contradicting = co2.model_copy(update={"option_id": "2-doublets"})
    assert "contradicts" in check_choice(choice_deps(question), contradicting)


def test_every_policy_record_of_a_stub_run_has_a_grounded_criterion(tmp_path):
    """Fix 4: no hollow rationale — each DR names a criterion whose key the evidence holds."""
    from gemini_agent.runner.design_runner import DesignRunner

    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path)
    runner.start("", run_id="c1")
    state = runner.load_state("c1")
    assert len(state.decision_records) == len(DR_TITLES)
    for record in state.decision_records:
        assert record.criterion is not None, record.dr_id
        assert record.criterion.key in record.rationale, record.dr_id
        assert "because it is recommended" not in record.rationale
    criteria = {r.dr_id: r.criterion.id for r in state.decision_records}
    assert criteria["DR-06"] == "db30-non-overlapping" and criteria["DR-08"] == "db31-answer"
    assert criteria["DR-10"] == "selected-not-rejected"
    assert criteria["DR-11"] == "opt-robust-backup"  # stubs: no robust variant to install
    assert criteria["DR-01"] == "generation-order-passed"  # stubs: no cost evidence yet


def test_kpi_criterion_needs_values_for_two_options(tmp_path):
    """Fix 4 (review): an option without data cannot lose, so one value decides nothing."""
    state = _with_economics(_state(tmp_path))
    state.kpis["C1-3d"] = KPISet(candidate_id="C1-3d")  # 3-doublets: no KPI values
    question = build_question("DR-01", state, Phase.P5, None)
    assert [c.id for c in question.criteria] == ["ranking-lowest-lcoh_per_mwh"]


# --------------------------------------------------------------------------- DR-11 (pre-L-2)
def _with_backup(tmp_path, *, p0=0.46, p1=0.90, change=2.05, meets=True, robust="C2-opt-robust"):
    """State after P8: TL-15 + the robust_backup step on the selected C2-opt."""
    from gemini_agent.runner.real_tools import RobustBackupToolOutput
    from gemini_agent.runner.state import RobustBackup

    state = _state(tmp_path)
    state.selected_id = "C2-opt"
    installed = {"electric_chiller_mw": 8.2, "peak_boiler_mw": 5.0}
    robust_mw = {"electric_chiller_mw": 8.2, "peak_boiler_mw": 10.9}
    out = RobustBackupToolOutput(
        candidate_id="C2-opt",
        robust_candidate_id=robust,
        robust_backup_percentile=90,
        installed_backup_mw=installed,
        robust_backup_mw=robust_mw,
        probability_demand_met=p0,
        probability_demand_met_robust=p1,
        kpis=[KPISet(candidate_id=robust or "x")],
    )
    for tr_id, tool, ids in (
        ("TR-010", "uncertainty", ["C2-opt"]),
        ("TR-011", "robust_backup", ["C2-opt"] + ([robust] if robust else [])),
    ):
        state.tool_results.append(
            ToolRecord(
                id=tr_id,
                tool=tool,
                phase=Phase.P8,
                args={"candidate_id": "C2-opt"},
                ok=True,
                candidate_ids=ids,
                source="workflow" if tool == "robust_backup" else "policy",
                data_keys=result_keys(out),
            )
        )
    state.robust_backup = RobustBackup(
        candidate_id="C2-opt",
        robust_candidate_id=robust,
        tool_result_id="TR-011",
        robust_backup_percentile=90,
        installed_backup_mw=installed,
        robust_backup_mw=robust_mw,
        probability_demand_met=p0,
        probability_demand_met_robust=p1,
        delta_lcoh_per_mwh=change,
        delta_lcoc_per_mwh=0.0,
        delta_lcoh_plus_lcoc_per_mwh=change,
        delta_capex_million=2.2,
        robust_meets_demand=meets,
        year_mode="full",
        reason="" if robust else "TL-15 robust need is within the installed backup nameplates",
    )
    state.kpis["C2-opt"] = KPISet(
        candidate_id="C2-opt", lcoh_per_mwh=42.5, lcoc_per_mwh=26.3, capex_million=27.9
    )
    if robust:
        state.kpis[robust] = KPISet(
            candidate_id=robust, lcoh_per_mwh=42.5 + change, lcoc_per_mwh=26.3, capex_million=30.1
        )
    return state


def test_dr11_offers_the_robust_backups_with_probability_and_cost_criteria(tmp_path):
    """OPT-ROBUST-BACKUP recommends the robust backups; the evidence criteria allow either."""
    state = _with_backup(tmp_path)
    question = build_question("DR-11", state, Phase.P8, None)
    assert question.role.value == "economics"
    assert [(o.id, o.eligible, o.recommended) for o in question.options] == [
        ("installed-backup", True, False),
        ("p90-robust-backup", True, True),
    ]
    assert "TR-011" in question.options[1].evidence_ids
    assert "config:assumptions.yaml#OPT-ROBUST-BACKUP" in question.options[1].evidence_ids
    best = {c.id: c.best_option_ids for c in question.criteria}
    assert list(best)[:3] == [
        "opt-robust-backup",
        "highest-probability_demand_met",
        "lowest-delta_lcoh_plus_lcoc_per_mwh",
    ]
    assert best["opt-robust-backup"] == best["highest-probability_demand_met"]
    assert best["highest-probability_demand_met"] == ["p90-robust-backup"]
    assert best["lowest-delta_lcoh_plus_lcoc_per_mwh"] == ["installed-backup"]
    assert best["lowest-lcoh_per_mwh"] == ["installed-backup"]  # KPI criteria from state.kpis
    for key in ("probability_demand_met", "delta_lcoh_per_mwh", "delta_lcoc_per_mwh"):
        assert key in question.evidence_keys
    # Policy: the register rule → the robust variant is the design to install.
    choice = PolicyEngine().choose(question, None).value
    assert (choice.option_id, choice.criterion) == ("p90-robust-backup", "opt-robust-backup")
    assert check_choice(choice_deps(question), choice) is None
    record = make_record(question, choice, "policy", state)
    assert installed_design(record, state) == "C2-opt-robust"
    # An engine may keep the installed backups on the cost criterion ...
    keep = OptionChoice(
        dr_id="DR-11",
        criterion="lowest-delta_lcoh_plus_lcoc_per_mwh",
        option_id="installed-backup",
        rationale="lowest delta_lcoh_plus_lcoc_per_mwh in robust_backup: no added backup cost",
    )
    assert check_choice(choice_deps(question), keep) is None
    kept = make_record(question, keep, "llm", state)
    assert installed_design(kept, state) == "C2-opt"
    # ... but not contradict its criterion.
    wrong = keep.model_copy(update={"option_id": "p90-robust-backup"})
    assert "contradicts" in check_choice(choice_deps(question), wrong)


def test_dr11_keeps_the_installed_backups_without_a_useful_variant(tmp_path):
    """No gain, unmet demand, a rejected variant or no variant ⇒ installed is recommended."""
    no_gain = build_question("DR-11", _with_backup(tmp_path / "a", p1=0.46), Phase.P8, None)
    assert [o.id for o in no_gain.options if o.recommended] == ["installed-backup"]
    assert no_gain.criteria[0].id == "opt-robust-backup"
    assert "highest-probability_demand_met" not in {c.id for c in no_gain.criteria}
    unmet = build_question("DR-11", _with_backup(tmp_path / "b", meets=False), Phase.P8, None)
    assert [o.id for o in unmet.options if o.eligible] == ["installed-backup"]
    assert "does not meet demand" in unmet.options[1].notes
    state = _with_backup(tmp_path / "c")
    state.verifications.append(
        Verification(
            candidate_id="C2-opt-robust",
            verdict=DesignVerdict.REJECTED,
            verifier="DesignVerifier",
            tool_result_id="TR-012",
            phase=Phase.P9,
        )
    )
    rejected = build_question("DR-11", state, Phase.P8, None)
    assert [o.id for o in rejected.options if o.eligible] == ["installed-backup"]
    assert "excluded" in rejected.options[1].notes
    none = build_question("DR-11", _with_backup(tmp_path / "d", robust=None), Phase.P8, None)
    assert "within the installed" in none.options[1].notes
    record = make_record(none, PolicyEngine().choose(none, None).value, "policy", state)
    assert record.choice == "installed-backup" and record.criterion.id == "opt-robust-backup"
    # A result for another design than the selected one is stale: no DR-11 yet.
    stale = _with_backup(tmp_path / "e")
    stale.selected_id = "C1-opt"
    assert build_question("DR-11", stale, Phase.P8, None) is None


def test_dr11_register_rule_and_percentile_check(tmp_path):
    """recommend: installed flips the recommendation; the percentile must equal UQ-MC's."""
    import shutil

    from gemini_agent.schemas.config import REPO_ROOT

    config = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", config)
    path = config / "assumptions.yaml"
    text = path.read_text("utf-8")
    assert text.count("recommend: robust\n") == 1
    path.write_text(text.replace("recommend: robust\n", "recommend: installed\n"), "utf-8")
    question = build_question("DR-11", _with_backup(tmp_path / "s"), Phase.P8, config)
    assert [o.id for o in question.options if o.recommended] == ["installed-backup"]
    assert "keeps installed_backup_mw" in question.criteria[0].text
    assert question.criteria[1].best_option_ids == ["p90-robust-backup"]  # still choosable
    path.write_text(
        text.replace(
            "recommend: robust\n      robust_backup_percentile: 90",
            "recommend: robust\n      robust_backup_percentile: 80",
        ),
        "utf-8",
    )
    with pytest.raises(ValueError, match="differs from UQ-MC"):
        robust_backup_rule(config)
