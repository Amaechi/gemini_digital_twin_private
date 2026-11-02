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
