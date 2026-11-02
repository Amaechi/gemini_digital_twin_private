"""Unit tests for gemini_agent.runner.decision_records (AG-07)."""

import json

import pytest

from gemini_agent.agents.engine import PolicyEngine
from gemini_agent.runner.decision_records import (
    build_options,
    build_question,
    make_record,
    write_records,
)
from gemini_agent.runner.state import RunState, ToolRecord, Verification
from gemini_agent.runner.tool_stubs import stub_toolbox
from gemini_agent.runner.toolbox import ToolContext
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import DesignVerdict, Phase
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
