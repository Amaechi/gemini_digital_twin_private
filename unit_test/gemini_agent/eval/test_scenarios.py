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
