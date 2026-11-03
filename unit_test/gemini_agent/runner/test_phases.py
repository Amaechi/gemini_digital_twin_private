"""Unit tests for gemini_agent.runner.phases and gemini_agent.runner.ranking."""

import pytest

from gemini_agent.agents.roles import ROLES
from gemini_agent.runner.phases import PHASE_ORDER, PHASE_SPECS, PhaseBudget, next_phase
from gemini_agent.runner.ranking import has_kpi_evidence, rank_candidates
from gemini_agent.runner.tool_stubs import stub_toolbox
from gemini_agent.runner.toolbox import ToolContext
from gemini_agent.schemas.decision import DR_TITLES, Phase
from gemini_agent.schemas.results import KPISet

pytestmark = pytest.mark.usefixtures("stub_tools")


def test_phase_table_is_complete_and_ordered():
    """P0–P10 in order, every DR made in exactly one phase, positive budgets."""
    assert tuple(PHASE_SPECS) == PHASE_ORDER == tuple(Phase)
    assert next_phase(Phase.P0) == Phase.P1 and next_phase(Phase.P10) is None
    drs = [dr for spec in PHASE_SPECS.values() for dr in spec.decision_records]
    assert sorted(drs) == sorted(DR_TITLES)
    toolbox = stub_toolbox()
    for spec in PHASE_SPECS.values():
        assert spec.budget.max_steps > len(spec.required_tools)
        assert spec.budget.max_tool_calls >= len(spec.required_tools)
        assert all(tool in toolbox for tool in spec.required_tools + spec.workflow_tools)
        assert set(spec.default_args) <= set(spec.required_tools + spec.workflow_tools)
        # Workflow steps belong to the runner, never to the agent (pre-L-2).
        assert not set(spec.workflow_tools) & set(ROLES[spec.role].tools), spec.phase
    assert PHASE_SPECS[Phase.P8].workflow_tools == ("robust_backup",)


def test_ranking_uses_lcoh_and_unmet_demand(tmp_path):
    """Lowest LCOH wins; known unmet demand ranks last; without KPIs: generation order."""
    toolbox = stub_toolbox()
    context = ToolContext(run_id="t", run_dir=tmp_path, config_dir=None)
    candidates = toolbox.execute("generate_candidates", {}, context).data.candidates
    assert rank_candidates(candidates, {})[:2] == ["C1-3d", "C2-2d"]
    kpis = {
        "C1-3d": KPISet(candidate_id="C1-3d", lcoh_per_mwh=40.0, unmet_heating_mwh=5.0),
        "C3-2d": KPISet(candidate_id="C3-2d", lcoh_per_mwh=55.0, unmet_heating_mwh=0.0),
        "C2-2d": KPISet(candidate_id="C2-2d", lcoh_per_mwh=50.0),
    }
    ranked = rank_candidates(candidates, kpis)
    assert ranked[:2] == ["C2-2d", "C3-2d"] and ranked[-1] == "C1-3d"
    assert "C0-0d" not in ranked
    assert has_kpi_evidence(kpis) and not has_kpi_evidence({"x": KPISet(candidate_id="x")})


def test_ranking_adds_lcoc_to_lcoh(tmp_path):
    """WP-14: the rank key is LCOH + LCOC (TL-13 objective), so cooling cost can flip the order."""
    toolbox = stub_toolbox()
    context = ToolContext(run_id="t", run_dir=tmp_path, config_dir=None)
    candidates = toolbox.execute("generate_candidates", {}, context).data.candidates
    kpis = {
        "C2-2d": KPISet(candidate_id="C2-2d", lcoh_per_mwh=30.0, lcoc_per_mwh=40.0),
        "C3-2d": KPISet(candidate_id="C3-2d", lcoh_per_mwh=35.0, lcoc_per_mwh=20.0),
    }
    assert rank_candidates(candidates, kpis)[:2] == ["C3-2d", "C2-2d"]


def test_p5_has_the_largest_token_budget():
    """Runner J: P5 (2 tools + 7 Decision Records) gets 90 k tokens; the default stays 60 k."""
    assert PHASE_SPECS[Phase.P5].budget.max_tokens == 90_000
    assert PhaseBudget().max_tokens == 60_000
    assert all(
        spec.budget.max_tokens <= PHASE_SPECS[Phase.P5].budget.max_tokens
        for spec in PHASE_SPECS.values()
    )
