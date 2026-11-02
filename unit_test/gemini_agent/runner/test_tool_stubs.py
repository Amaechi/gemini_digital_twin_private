"""Unit tests for gemini_agent.runner.toolbox and the labelled tool stubs."""

import pytest

from gemini_agent.runner.tool_stubs import GAP_IDS, stub_specs, stub_toolbox
from gemini_agent.runner.toolbox import ToolArgsError, ToolContext
from gemini_agent.schemas.decision import DesignVerdict


@pytest.fixture()
def context(tmp_path):
    """Tool context with the stub candidates C0–C8 and C2 selected."""
    toolbox = stub_toolbox()
    base = ToolContext(run_id="t", run_dir=tmp_path, config_dir=None)
    candidates = toolbox.execute("generate_candidates", {}, base).data.candidates
    return ToolContext(
        run_id="t",
        run_dir=tmp_path,
        config_dir=None,
        candidates=tuple(candidates),
        shortlist=("C2-2d",),
        selected_id="C2-2d",
    )


def test_every_stub_is_labelled(context):
    """Stub results say STUB, carry a warning naming the owning WP and produce no KPI numbers."""
    toolbox = stub_toolbox()
    for spec in stub_specs():
        if not spec.stub:
            continue
        args = {"card_id": "RC-04"} if spec.name == "read_reference_card" else {}
        result = toolbox.execute(spec.name, args, context)
        assert result.summary.startswith("STUB"), spec.name
        assert any(spec.owner in warning for warning in result.warnings), spec.name
        for kpi in getattr(result.data, "kpis", []):
            assert kpi.lcoh_per_mwh is None and kpi.capex_million is None


def test_candidates_follow_spec_table(context):
    """C0–C8 from spec §3.4; return temperatures from DB-31/DB-32; answers override them."""
    concepts = [c.concept for c in context.candidates]
    assert concepts == [f"C{i}" for i in range(9)]
    by_concept = {c.concept: c for c in context.candidates}
    assert by_concept["C0"].doublets == 0 and by_concept["C4"].depth_m == 3200.0
    assert by_concept["C7"].gas_handling.value == "pressurised_reinject"
    assert {c.heating_return_degc for c in context.candidates} == {55.0}
    toolbox = stub_toolbox()
    answered = ToolContext(
        run_id="t", run_dir=context.run_dir, config_dir=None, answers={"DB-31": 60.0}
    )
    assert {
        c.heating_return_degc
        for c in toolbox.execute("generate_candidates", {}, answered).data.candidates
    } == {60.0}


def test_guard_verify_and_design_basis(context):
    """Guard checks limits; the stub verifier is never ACCEPTED; gaps DB-30…32 are listed."""
    toolbox = stub_toolbox()
    bad = context.candidates[2].model_copy(update={"doublets": 4, "design_flow_m3h": 400.0})
    guard_ctx = ToolContext(run_id="t", run_dir=context.run_dir, config_dir=None, candidates=(bad,))
    check = toolbox.execute("design_guard", {}, guard_ctx).data.checks[0]
    assert not check.passed and len(check.violations) == 2
    verdict = toolbox.execute("verify_design", {"candidate_id": "all"}, context).data
    assert verdict.verdict == DesignVerdict.UNVERIFIED and verdict.candidate_id == "C2-2d"
    basis = toolbox.execute("load_design_basis", {}, context).data
    assert tuple(basis.open_gaps) == GAP_IDS


def test_argument_validation_and_unknown_candidate(context):
    """Flat argument schemas reject unknown keys; unknown candidate ids raise."""
    toolbox = stub_toolbox()
    with pytest.raises(ToolArgsError, match="simulate_annual"):
        toolbox.validate_args("simulate_annual", {"mode": "slow"})
    with pytest.raises(ToolArgsError):
        toolbox.validate_args("design_guard", {"candidate": "C1-3d"})
    with pytest.raises(ValueError, match="unknown candidate_id"):
        toolbox.execute("simulate_annual", {"candidate_id": "C9-9d"}, context)
    with pytest.raises(ValueError, match="runner"):
        toolbox.execute("request_missing_input", {}, context)
    assert "one of fast|full" in toolbox.brief("simulate_annual").args["mode"]
