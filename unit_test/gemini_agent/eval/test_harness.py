"""Unit tests for gemini_agent.eval.harness (spec §12 EV-01/EV-04; WP-13) — offline only.

The policy and TestModel suites are the CI evaluation (WP-13 acceptance); ``--engine llm`` is
exercised with a FunctionModel in place of Ollama.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from gemini_agent.agents import engine as engine_module
from gemini_agent.eval import harness
from gemini_agent.eval.harness import (
    EvalResults,
    Outcome,
    compare_evals,
    eval_dir_name,
    model_label,
    prepare_config,
    removed_components,
    run_scenario,
    run_suite,
)
from gemini_agent.eval.scenarios import Scenario, evaluate, load_scenarios
from gemini_agent.runner import design_runner
from gemini_agent.schemas.config import load_config
from gemini_simulator.registry import COMPONENT_REGISTRY
from unit_test.gemini_agent.llm_fakes import fake_model

pytestmark = pytest.mark.usefixtures("stub_tools")

EXPECTED_POLICY = {  # on the stub catalogue (``stub_tools``): fast CI of the harness itself
    "S1": Outcome.FAIL,
    "S2": Outcome.PASS,
    "S3": Outcome.PASS,
    "S4": Outcome.PASS,
    "S5": Outcome.PASS,
    "S6": Outcome.FAIL,
    "S7": Outcome.PENDING,
    "S8": Outcome.FAIL,
    "S9": Outcome.PASS,
    "S10": Outcome.SKIPPED,
    "S11": Outcome.SKIPPED,
}
STUB_ONLY_FAILURES = {
    "S1": {"verdict", "stub_tool_calls_max"},
    "S6": {"tool_output_mentions"},
    "S8": {"dr_mentions", "cross_run_sizing"},
}
"""WP-14 (and the WP-13 follow-up's S8 cross-run check) promoted these checks; they need the
real tools (``scripts/run_eval.py``), so on the stub catalogue exactly they fail — every other
active check passes (S2 and S5 pass on stubs: the intake questions are the runner's own)."""


@pytest.fixture(scope="module")
def policy_results(tmp_path_factory, stub_tools) -> EvalResults:
    """Run the full suite on the deterministic policy engine (CI)."""
    root = tmp_path_factory.mktemp("eval")
    return run_suite(load_scenarios(), engine="policy", eval_root=root)


@pytest.fixture(scope="module")
def testmodel_results(tmp_path_factory, stub_tools) -> EvalResults:
    """Run the full suite through the Pydantic AI agents with TestModel (CI)."""
    root = tmp_path_factory.mktemp("eval")
    return run_suite(load_scenarios(), engine="testmodel", eval_root=root)


def _run(results: EvalResults, scenario: str):
    return next(run for run in results.runs if run.scenario == scenario)


def _failed_checks(results: EvalResults) -> dict[str, set[str]]:
    return {
        run.scenario: {check.type for check in run.checks if not check.passed}
        for run in results.runs
        if any(not check.passed for check in run.checks)
    }


def test_policy_suite_outcomes(policy_results):
    """On stubs only the WP-14 real-tool checks fail; S1 stays UNVERIFIED (golden rule 3)."""
    assert {run.scenario: run.outcome for run in policy_results.runs} == EXPECTED_POLICY
    assert _failed_checks(policy_results) == STUB_ONLY_FAILURES
    s1 = _run(policy_results, "S1")
    assert s1.verdict == "UNVERIFIED" and s1.metrics.stub_tool_calls > 0
    assert all(run.metrics.llm_calls == 0 for run in policy_results.runs)
    s10 = _run(policy_results, "S10")
    assert s10.note.startswith("needs an LLM engine") and not s10.run_dirs


def test_policy_suite_artefacts_and_report(policy_results):
    """eval_results.json round-trips; the Markdown report has the EV-04 columns."""
    eval_dir = Path(policy_results.eval_dir)
    assert eval_dir.name == "policy"
    stored = EvalResults.model_validate_json((eval_dir / "eval_results.json").read_text())
    assert stored == policy_results
    report = (eval_dir / "EVAL_REPORT.md").read_text()
    for column in ("Success", "Tool calls", "Retries", "Fallbacks", "Tokens", "Runtime s"):
        assert column in report
    assert "PENDING (L-2): the Model Builder writes the model" in report
    for scenario in ("S1", "S3", "S9"):
        assert (eval_dir / scenario / "events.jsonl").exists()
    s2 = _run(policy_results, "S2")  # S2: one intake question, nothing loaded or assumed
    assert s2.status == "paused" and s2.metrics.tool_calls == 0
    assert any("CFG-INPUT-DEFAULTS" in check.detail for check in s2.checks)
    s5 = _run(policy_results, "S5")  # S5: the register default keeps the basis in assume mode
    assert s5.status == "completed"
    assert any("CFG-BASIS-CONFIRM = keep" in check.detail for check in s5.checks)
    s8 = _run(policy_results, "S8")  # config override applied to a copy, not to config/
    cross = next(check for check in s8.checks if check.type == "cross_run_sizing")
    assert "no PI" in cross.detail  # stubs: the S1 baseline is there but has no TL-04 output
    assert yaml.safe_load((eval_dir / "S8.config" / "design_basis.yaml").read_text())["reservoir"][
        "permeability_md"
    ] == pytest.approx(50.0)
    assert load_config("design_basis").reservoir.permeability_md != 50.0
    assert s8.checks[0].detail == "reservoir.permeability_md = 50.0"
    assert "absorption_chiller" in COMPONENT_REGISTRY  # S7 restored the registry


def test_testmodel_suite_exercises_retries_fallbacks_and_tokens(testmodel_results):
    """Every TestModel call is retried, logged and falls back to policy (it never validates)."""
    assert testmodel_results.model == "testmodel"
    assert _failed_checks(testmodel_results) == STUB_ONLY_FAILURES
    outcomes = {run.scenario: run.outcome for run in testmodel_results.runs}
    assert outcomes == {**EXPECTED_POLICY, "S10": Outcome.PENDING, "S11": Outcome.PENDING}
    s1 = _run(testmodel_results, "S1").metrics
    assert s1.llm_calls > 0 and s1.fallbacks == s1.llm_calls and s1.retries > 0
    assert s1.total_tokens > 0 and s1.tool_call_validity_frac == 0.0
    eval_dir = Path(testmodel_results.eval_dir)
    calls = (eval_dir / "S1" / "llm_calls.jsonl").read_text().splitlines()
    assert len(calls) == s1.llm_calls  # transcript: one line per LLM call
    assert json.loads(calls[0])["messages"]


def test_testmodel_s10_compares_naive_and_optimised_tokens(testmodel_results):
    """S10: the naive profile (full history) costs more tokens than the optimised one."""
    s10 = _run(testmodel_results, "S10")
    eval_dir = Path(testmodel_results.eval_dir)
    assert s10.run_dirs == ["S10-naive", "S10-optimised"]
    stored = json.loads((eval_dir / "S10-token_comparison.json").read_text())
    total = stored["total"]
    assert total == s10.token_comparison["total"]
    assert 0 < total["optimised_tokens"] < total["naive_tokens"]
    report = (eval_dir / "EVAL_REPORT.md").read_text()
    assert "S10 naive vs optimised tokens" in report and "naive = full event history" in report
    assert report.index("| P9 |") < report.index("| P10 |")
    assert design_runner.HISTORY_EVENTS == 12  # naive profile restored the default


def test_llm_engine_records_to_the_model_directory(tmp_path, monkeypatch):
    """--engine llm writes runs/eval/<model>/ with transcripts; Ollama replaced offline."""
    tags = []

    def fake_build(llm_config, kind="orchestrator", tag=""):
        tags.append(tag)
        return fake_model()

    monkeypatch.setattr(engine_module, "build_ollama_model", fake_build)
    results = run_suite(
        load_scenarios(),
        engine="llm",
        eval_root=tmp_path,
        scenario_ids=["S1", "S3", "S11"],
        model_tag="qwen3.5:9b",
    )
    assert results.model == "qwen3.5:9b" and set(tags) == {"qwen3.5:9b"}
    assert _failed_checks(results) == {"S1": STUB_ONLY_FAILURES["S1"]}  # stub catalogue
    eval_dir = tmp_path / "qwen3.5_9b"
    assert Path(results.eval_dir) == eval_dir
    assert (eval_dir / "S1" / "llm_calls.jsonl").exists()
    assert (eval_dir / "S1" / "token_summary.json").exists()
    s11 = _run(results, "S11").metrics
    assert s11.llm_calls > 0 and s11.tool_call_validity_frac == 1.0 and s11.fallbacks == 0


def test_llm_engine_without_model_uses_the_configured_profile(tmp_path, monkeypatch):
    """Without --model each role builds its configured model (orchestrator/coder), as the CLI."""
    built = []

    def fake_build(llm_config, kind="orchestrator", tag=""):
        built.append((kind, tag))
        return fake_model()

    monkeypatch.setattr(engine_module, "build_ollama_model", fake_build)
    results = run_suite(load_scenarios(), engine="llm", eval_root=tmp_path, scenario_ids=["S3"])
    config = load_config("llm")
    orchestrator = config.profiles[config.active_profile].orchestrator
    assert results.model == orchestrator and (tmp_path / eval_dir_name(orchestrator)).is_dir()
    assert built and all(tag == "" for _, tag in built)  # no tag forced onto the coder role


def test_compare_evals_writes_the_model_comparison(policy_results, testmodel_results, tmp_path):
    """S11 comparison table across eval directories."""
    dirs = [policy_results.eval_dir, testmodel_results.eval_dir]
    comparison = compare_evals(dirs, tmp_path)
    models = {m["model"]: m for m in comparison["models"]}
    assert set(models) == {"policy", "testmodel"}
    assert models["testmodel"]["fallbacks"] > 0 and models["policy"]["total_tokens"] == 0
    assert models["policy"]["outcomes"]["skipped"] == 2
    assert models["policy"]["kind_coercions"] == 0  # KIND_COERCED events (LLM engines only)
    assert models["policy"]["token_budget_overruns"] == 0  # TOKEN_BUDGET_OVERRUN (runner J)
    stored = json.loads((tmp_path / "model_comparison.json").read_text())
    assert stored["models"] == comparison["models"]
    table = (tmp_path / "model_comparison.md").read_text()
    assert "| testmodel | testmodel |" in table and "Tool-call validity" in table
    assert "| Overruns |" in table


def test_failing_check_fails_the_scenario(tmp_path):
    """An unmet expectation is reported as FAIL with the observed value."""
    scenario = Scenario.model_validate(
        {
            "id": "S99",
            "situation": "s",
            "expected": "e",
            "prompt": "What's the best pizza in Delft?",
            "checks": [{"type": "status", "equals": "completed", "expect": "completes"}],
        }
    )
    run = run_scenario(scenario, engine="policy", eval_dir=tmp_path)
    assert run.outcome == Outcome.FAIL and run.checks[0].detail == "status refused"


def test_broken_setup_is_an_error_not_a_crash(tmp_path):
    """A scenario that cannot be set up reports ERROR; the suite carries on."""
    scenario = Scenario.model_validate(
        {
            "id": "S98",
            "situation": "s",
            "expected": "e",
            "setup": {"remove_components": ["no_such_model"]},
            "checks": [{"type": "status", "equals": "completed", "expect": "completes"}],
        }
    )
    run = run_scenario(scenario, engine="policy", eval_dir=tmp_path)
    assert run.outcome == Outcome.ERROR and "no_such_model" in run.note


def test_phases_in_order_allows_verifier_loop_backs():
    """S1: a REJECTED → P6 loop-back is valid; a skipped or reordered phase is not."""
    words = [f"P{n}" for n in range(11)]
    check = Scenario.model_validate(
        {
            "id": "S1",
            "situation": "s",
            "expected": "e",
            "checks": [{"type": "phases_in_order", "words": words, "expect": "x"}],
        }
    ).checks[0]
    looped = SimpleNamespace(phases_started=words + ["P6", "P7", "P8", "P9", "P10"])
    assert evaluate(check, looped).passed
    assert not evaluate(check, SimpleNamespace(phases_started=words[:-1])).passed
    swapped = SimpleNamespace(phases_started=words[:3] + ["P4", "P3"] + words[5:])
    assert not evaluate(check, swapped).passed


def test_removed_components_restores_the_registry():
    """S7 setup removes a registry entry only for the duration of the run."""
    entry = COMPONENT_REGISTRY["free_cooling"]
    with removed_components(["free_cooling"]):
        assert "free_cooling" not in COMPONENT_REGISTRY
    assert COMPONENT_REGISTRY["free_cooling"] is entry
    with pytest.raises(RuntimeError), removed_components(["free_cooling"]):
        raise RuntimeError("boom")
    assert COMPONENT_REGISTRY["free_cooling"] is entry


def test_prepare_config_merges_and_validates(tmp_path):
    """Overrides are deep-merged into a copy and validated against the config schema."""
    target = prepare_config({"limits": {"flow_max_m3h": 250.0}}, tmp_path / "cfg")
    assert load_config("limits", target).flow_max_m3h == 250.0
    assert (
        load_config("limits", target).doublet_count_max == load_config("limits").doublet_count_max
    )
    with pytest.raises(ValidationError):
        prepare_config({"limits": {"flow_max_m3h": "fast"}}, tmp_path / "bad")


def test_clear_run_dir_refuses_foreign_directories(tmp_path):
    """Only earlier run directories are deleted before a re-run."""
    foreign = tmp_path / "S1"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("keep me")
    with pytest.raises(FileExistsError):
        harness._clear_run_dir(foreign)
    assert (foreign / "notes.txt").exists()


def test_model_labels_and_directory_names():
    """Results of engine llm go to the sanitised Ollama tag (default: the active profile's)."""
    config = load_config("llm")
    assert model_label("policy") == "policy" and model_label("testmodel") == "testmodel"
    assert model_label("llm", "", config) == config.profiles[config.active_profile].orchestrator
    assert eval_dir_name("qwen3.6:35b-a3b") == "qwen3.6_35b-a3b"
    assert eval_dir_name("../x") == "x"
