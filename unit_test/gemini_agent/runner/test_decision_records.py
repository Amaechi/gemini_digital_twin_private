"""Unit tests for gemini_agent.runner.decision_records (AG-07)."""

import json

import pytest

from gemini_agent.agents.engine import PolicyEngine, check_choice, choice_deps
from gemini_agent.runner.decision_records import (
    build_options,
    build_question,
    make_record,
    result_keys,
    write_records,
)
from gemini_agent.runner.state import RunState, ToolRecord, Verification
from gemini_agent.runner.tool_stubs import KPIBatch, stub_toolbox
from gemini_agent.runner.toolbox import ToolContext
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import DesignVerdict, OptionChoice, Phase
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
    assert len(state.decision_records) == 10
    for record in state.decision_records:
        assert record.criterion is not None, record.dr_id
        assert record.criterion.key in record.rationale, record.dr_id
        assert "because it is recommended" not in record.rationale
    criteria = {r.dr_id: r.criterion.id for r in state.decision_records}
    assert criteria["DR-06"] == "db30-non-overlapping" and criteria["DR-08"] == "db31-answer"
    assert criteria["DR-10"] == "selected-not-rejected"
    assert criteria["DR-01"] == "generation-order-passed"  # stubs: no cost evidence yet


def test_kpi_criterion_needs_values_for_two_options(tmp_path):
    """Fix 4 (review): an option without data cannot lose, so one value decides nothing."""
    state = _with_economics(_state(tmp_path))
    state.kpis["C1-3d"] = KPISet(candidate_id="C1-3d")  # 3-doublets: no KPI values
    question = build_question("DR-01", state, Phase.P5, None)
    assert [c.id for c in question.criteria] == ["ranking-lowest-lcoh_per_mwh"]
