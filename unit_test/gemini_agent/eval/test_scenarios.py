"""Unit tests for gemini_agent.eval.scenarios (spec §12 EV-01; WP-13)."""

import re

import pytest
from pydantic import ValidationError

from gemini_agent.eval import scenarios as scenarios_module
from gemini_agent.eval.scenarios import (
    ENGINE_KINDS,
    Check,
    Pending,
    Scenario,
    ScenarioSuite,
    load_scenarios,
)
from gemini_agent.schemas.config import REPO_ROOT

SPEC = REPO_ROOT / "docs" / "hackathon" / "GEOAGENT_DESIGN_SPEC.md"


def _spec_table() -> dict[str, tuple[str, str]]:
    """Spec §12 rows: id -> (situation, expected behaviour)."""
    rows = {}
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\| (S\d+) \| (.+?) \| (.+?) \|$", line)
        if match:
            rows[match.group(1)] = (match.group(2), match.group(3))
    return rows


def test_suite_covers_s1_to_s11_with_the_spec_text():
    """Every spec §12 scenario is present with its expected behaviour copied verbatim."""
    suite = load_scenarios()
    spec = _spec_table()
    assert [s.id for s in suite.scenarios] == [f"S{n}" for n in range(1, 12)] == list(spec)
    for scenario in suite.scenarios:
        assert scenario.expected == spec[scenario.id][1]
        assert scenario.checks, scenario.id  # at least one expected-behaviour assertion


def test_pending_items_state_needs_and_reason():
    """Nothing is stubbed silently: each pending item names the WPs it needs and why."""
    suite = load_scenarios()
    pending = {s.id: s.pending for s in suite.scenarios}
    assert not pending["S1"]  # WP-14 promoted ACCEPTED + 0 stub calls to checks
    s1 = {check.type: check for check in suite.get("S1").checks}
    assert s1["verdict"].equals == "ACCEPTED" and s1["stub_tool_calls_max"].value == 0
    for scenario in suite.scenarios:
        for item in scenario.pending:
            assert item.needs and item.reason and item.expect
    assert {s.id for s in suite.scenarios if set(s.engines) != set(ENGINE_KINDS)} == {"S10", "S11"}


def test_every_check_type_has_an_evaluator():
    """The check vocabulary and the evaluators match one-to-one."""
    assert set(scenarios_module._EVALUATORS) == set(scenarios_module.CheckType.__args__)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"type": "status", "expect": "x"}, "needs \\['equals'\\]"),
        ({"type": "files", "expect": "x"}, "needs \\['files'\\]"),
        ({"type": "unknown", "expect": "x"}, "type"),
        ({"type": "status", "equals": "completed", "expect": ""}, "expect"),
    ],
)
def test_invalid_checks_are_rejected(raw, message):
    """A check without its arguments fails at load time, not during an eval."""
    with pytest.raises(ValidationError, match=message):
        Check.model_validate(raw)


def test_scenario_and_suite_validation():
    """Engine subsets need a skip reason; ids are unique; pending needs a WP."""
    check = {"type": "status", "equals": "completed", "expect": "completes"}
    base = {"id": "S1", "situation": "s", "expected": "e", "checks": [check]}
    with pytest.raises(ValidationError, match="skip_reason"):
        Scenario.model_validate({**base, "engines": ["llm"]})
    with pytest.raises(ValidationError, match="checks"):
        Scenario.model_validate({**base, "checks": []})
    with pytest.raises(ValidationError, match="token_profiles"):
        Scenario.model_validate({**base, "setup": {"token_profiles": ["naive"]}})
    with pytest.raises(ValidationError, match="duplicate"):
        ScenarioSuite.model_validate({"source": "x", "scenarios": [base, base]})
    with pytest.raises(ValidationError, match="needs"):
        Pending.model_validate({"expect": "x", "needs": [], "reason": "r"})
    suite = ScenarioSuite.model_validate({"source": "x", "scenarios": [base]})
    with pytest.raises(KeyError, match="S9"):
        suite.get("S9")


def test_tool_output_mentions_reads_the_latest_output_at_a_path(tmp_path):
    """WP-14 (S6): words must appear in the tool output at the dotted path, not elsewhere."""
    import json

    from gemini_agent.eval.scenarios import Observation, evaluate
    from gemini_agent.runner.design_runner import RunResult
    from gemini_agent.runner.state import RunState, ToolRecord
    from gemini_agent.schemas.decision import Phase, RunStatus

    (tmp_path / "tool_results").mkdir()
    output = {"data": {"cases": [{"assumption_id": "DB-31", "value": 45}]}, "assumption_ids": ["X"]}
    (tmp_path / "tool_results" / "TR-001_uncertainty.json").write_text(json.dumps(output))
    state = RunState(run_id="r", user_prompt="design", engine="policy", input_mode="assume")
    state.tool_results.append(
        ToolRecord(
            id="TR-001",
            tool="uncertainty",
            phase=Phase.P8,
            args={},
            ok=True,
            stub=False,
            source="policy",
            path="tool_results/TR-001_uncertainty.json",
        )
    )
    result = RunResult("r", RunStatus.COMPLETED, Phase.P10, None, None, "", tmp_path)
    obs = Observation(result=result, state=state, run_dir=tmp_path, phases_started=[])

    def check(**kw):
        return Check(type="tool_output_mentions", expect="e", tool="uncertainty", **kw)

    assert evaluate(check(path="data.cases", words=["DB-31"]), obs).passed
    assert not evaluate(check(path="data.cases", words=["X"]), obs).passed  # outside the path
    assert evaluate(check(words=["X"]), obs).passed  # whole output without a path
    missing = evaluate(Check(type="tool_output_mentions", expect="e", tool="co2", words=["a"]), obs)
    assert not missing.passed and "not called" in missing.detail


# --------------------------------------------------------------------------- S8 cross-run check
def _sized_run(
    root, run_id: str, *, pi: float, stages: int, setting_depth_m: float, peak_mw: float, **wells
):
    """A finished run directory: SQLite state, TL-04/TL-05 outputs and the annual CSV."""
    import json

    from gemini_agent.memory.event_store import SQLiteEventStore
    from gemini_agent.runner.design_runner import STORE_FILE
    from gemini_agent.runner.state import RunState, ToolRecord
    from gemini_agent.schemas.candidate import Candidate
    from gemini_agent.schemas.common import GasHandling
    from gemini_agent.schemas.decision import Phase, RunStatus

    run_dir = root / run_id
    (run_dir / "tool_results").mkdir(parents=True)
    design = Candidate(
        id="C2-opt",
        concept="C2",
        doublets=wells.get("doublets", 2),
        depth_m=wells.get("depth_m", 2500.0),
        design_flow_m3h=wells.get("design_flow_m3h", 300.0),
        gas_handling=GasHandling.DEGAS_USE,
        heating_return_degc=55.0,
        cooling_return_degc=26.0,
    )
    state = RunState(
        run_id=run_id,
        user_prompt="design",
        engine="policy",
        input_mode="assume",
        status=RunStatus.COMPLETED,
        candidates=[design],
        selected_id=design.id,
    )
    outputs = {
        "productivity_index": {"data": {"productivity_index_m3h_per_bar": pi}},
        "doublet_operating_point": {
            "data": {"doublet": {"esp": {"stages": stages, "setting_depth_m": setting_depth_m}}}
        },
    }
    for number, (tool, output) in enumerate(outputs.items(), start=1):
        path = f"tool_results/TR-{number:03d}_{tool}.json"
        (run_dir / path).write_text(json.dumps(output), encoding="utf-8")
        state.tool_results.append(
            ToolRecord(
                id=f"TR-{number:03d}",
                tool=tool,
                phase=Phase.P2,
                args={},
                ok=True,
                source="policy",
                path=path,
            )
        )
    (run_dir / f"annual_{design.id}_fast.csv").write_text(
        "day,elec_esp_1_mw,elec_esp_2_mw,heat_pumps_on\n"
        f"1,{peak_mw / 2},{peak_mw},True\n2,{peak_mw / 3},{peak_mw / 4},False\n",
        encoding="utf-8",
    )
    store = SQLiteEventStore(run_dir / STORE_FILE)
    store.create_run(run_id, state.status.value, state.model_dump(mode="json"))
    store.close()
    return state, run_dir


def _observe(state, run_dir):
    from gemini_agent.eval.scenarios import Observation
    from gemini_agent.runner.design_runner import RunResult

    result = RunResult(
        state.run_id, state.status, state.phase, None, state.selected_id, "", run_dir
    )
    return Observation(result=result, state=state, run_dir=run_dir, phases_started=[])


def test_cross_run_sizing_passes_when_the_esp_or_the_wells_grow_at_a_lower_pi(tmp_path):
    """S8 (WP-13 follow-up): the run is compared with the baseline scenario of the same eval."""
    from gemini_agent.eval.scenarios import evaluate, well_sizing

    _sized_run(tmp_path, "S1", pi=12.22, stages=16, setting_depth_m=840.0, peak_mw=0.46)
    check = Check(type="cross_run_sizing", baseline="S1", expect="re-sized")

    # ESP re-sized (more stages, deeper, more power) at the same wells, as in the final eval.
    state, run_dir = _sized_run(
        tmp_path, "S8", pi=3.06, stages=49, setting_depth_m=1560.0, peak_mw=1.43
    )
    outcome = evaluate(check, _observe(state, run_dir))
    assert outcome.passed and outcome.detail.startswith("ESP re-sized: PI 3.06 vs S1 12.22")
    assert "ESP 49 stages @ 1560 m, peak 1.43 MW (fast year)" in outcome.detail
    sizing = well_sizing(state, run_dir)
    assert (sizing.wells, sizing.esp_stages, sizing.esp_peak_electric_mw) == (
        (2, 2500.0, 300.0),
        49,
        1.43,
    )

    # Wells re-sized (3 doublets at a lower flow) with the same ESP.
    state, run_dir = _sized_run(
        tmp_path, "S8b", pi=3.06, stages=16, setting_depth_m=840.0, peak_mw=0.46, doublets=3
    )
    outcome = evaluate(check, _observe(state, run_dir))
    assert outcome.passed and outcome.detail.startswith("wells re-sized")

    # Nothing grew (peak within the 5 % tolerance, fewer doublets): not re-sized for a lower PI.
    state, run_dir = _sized_run(
        tmp_path, "S8c", pi=3.06, stages=16, setting_depth_m=840.0, peak_mw=0.47, doublets=1
    )
    outcome = evaluate(check, _observe(state, run_dir))
    assert not outcome.passed and outcome.detail.startswith("neither the wells nor the ESP")

    # No lower PI than the baseline: the comparison is meaningless, so it fails.
    state, run_dir = _sized_run(
        tmp_path, "S8d", pi=12.22, stages=49, setting_depth_m=1560.0, peak_mw=1.43
    )
    outcome = evaluate(check, _observe(state, run_dir))
    assert not outcome.passed and outcome.detail.startswith("no lower PI than S1")


def test_cross_run_sizing_reports_a_missing_baseline_or_evidence(tmp_path):
    """Without S1 in the eval directory (or without a PI, e.g. stubs) the check fails, with why."""
    from gemini_agent.eval.scenarios import evaluate
    from gemini_agent.runner.state import RunState

    state, run_dir = _sized_run(
        tmp_path, "S8", pi=3.06, stages=49, setting_depth_m=1560.0, peak_mw=1.43
    )
    check = Check(type="cross_run_sizing", baseline="S1", expect="re-sized")
    outcome = evaluate(check, _observe(state, run_dir))
    assert not outcome.passed and "baseline S1 not found" in outcome.detail

    (tmp_path / "S1").mkdir()  # a directory without a run
    assert "baseline S1 not found" in evaluate(check, _observe(state, run_dir)).detail

    bare = RunState(run_id="S1", user_prompt="design", engine="policy", input_mode="assume")
    from gemini_agent.memory.event_store import SQLiteEventStore
    from gemini_agent.runner.design_runner import STORE_FILE

    store = SQLiteEventStore(tmp_path / "S1" / STORE_FILE)
    store.create_run("S1", "completed", bare.model_dump(mode="json"))
    store.close()
    outcome = evaluate(check, _observe(state, run_dir))
    assert not outcome.passed and "no PI" in outcome.detail
    with pytest.raises(ValidationError, match="needs \\['baseline'\\]"):
        Check.model_validate({"type": "cross_run_sizing", "expect": "x"})
