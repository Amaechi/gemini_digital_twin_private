"""CLI tests for scripts/run_eval.py and scripts/validate_submission.py (WP-13) — offline only."""

import importlib.util
import json
import os
import subprocess
import sys

import pytest
import yaml

from gemini_agent.agents.engine import PolicyEngine
from gemini_agent.runner.design_runner import DesignRunner
from gemini_agent.schemas.config import REPO_ROOT

pytestmark = pytest.mark.usefixtures("stub_tools")

SCRIPTS = REPO_ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"wp13_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run_eval():
    """scripts/run_eval.py as a module."""
    return _load("run_eval")


@pytest.fixture(scope="module")
def validate():
    """scripts/validate_submission.py as a module."""
    return _load("validate_submission")


@pytest.mark.parametrize("name", ["run_eval", "validate_submission"])
def test_scripts_run_without_pythonpath(name):
    """The scripts find src/ themselves and carry the Plant Debugger header."""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / f"{name}.py"), "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    head = (SCRIPTS / f"{name}.py").read_text(encoding="utf-8").splitlines()[1:3]
    assert "Adapted from marcosanyo/PlantDebugger" in head[0] and "@ d69507a" in head[1]


def test_run_eval_policy_subset_and_compare(run_eval, tmp_path, capsys):
    """--engine policy --scenarios runs a subset; --compare writes the model comparison."""
    assert run_eval.main(["--scenarios", "S3,S4", "--eval-root", str(tmp_path)]) == 0
    results = json.loads((tmp_path / "policy" / "eval_results.json").read_text())
    assert [run["scenario"] for run in results["runs"]] == ["S3", "S4"]
    assert "S3: pass" in capsys.readouterr().out
    assert run_eval.main(["--compare", str(tmp_path / "policy"), "--eval-root", str(tmp_path)]) == 0
    assert (tmp_path / "model_comparison.md").exists()


def test_run_eval_usage_errors_and_failures(run_eval, tmp_path):
    """--model needs --engine llm; a failing scenario gives exit code 1."""
    assert run_eval.main(["--model", "qwen3.5:9b", "--eval-root", str(tmp_path)]) == 2
    assert run_eval.main(["--scenarios", "S99", "--eval-root", str(tmp_path)]) == 2
    suite = {
        "source": "test",
        "scenarios": [
            {
                "id": "S1",
                "situation": "s",
                "expected": "e",
                "prompt": "What's the best pizza in Delft?",
                "checks": [{"type": "status", "equals": "completed", "expect": "completes"}],
            }
        ],
    }
    path = tmp_path / "scenarios.yaml"
    path.write_text(yaml.safe_dump(suite))
    args = ["--scenarios-file", str(path), "--eval-root", str(tmp_path)]
    assert run_eval.main(args) == 1


def test_validate_submission_fails_on_a_stub_run(validate, tmp_path, capsys):
    """Exit 1 on today's stub run; the Markdown and JSON reports are written."""
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path / "final")
    try:
        runner.start("", run_id="r")
    finally:
        runner.close()
    md, js = tmp_path / "out" / "check.md", tmp_path / "out" / "check.json"
    args = ["--run-dir", str(tmp_path / "final"), "--eval-root", str(tmp_path / "eval")]
    assert validate.main([*args, "--output-md", str(md), "--output-json", str(js)]) == 1
    assert "NOT READY" in capsys.readouterr().out and "NOT READY" in md.read_text()
    report = json.loads(js.read_text())
    failed = {c["id"] for c in report["checks"] if c["status"] == "fail"}
    assert {"SUB-03", "SUB-04", "SUB-05"} <= failed
