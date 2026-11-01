"""Unit tests for gemini_agent.cli (offline; the llm engine uses a FunctionModel)."""

import json
import os
import subprocess
import sys

import pytest

from gemini_agent import __version__
from gemini_agent.agents import engine as engine_module
from gemini_agent.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    EXIT_PAUSED,
    EXIT_REFUSED,
    EXIT_USAGE,
    main,
)
from gemini_agent.schemas.config import REPO_ROOT
from unit_test.gemini_agent.llm_fakes import fake_model


def test_module_help_runs_as_subprocess():
    """`python -m gemini_agent.cli --help` works (WP-00 acceptance)."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "gemini_agent.cli", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "geoagent" in proc.stdout
    assert "run" in proc.stdout and "validate-config" in proc.stdout


def test_version_and_run_help(capsys):
    """--version prints the package version; `run --help` lists the engine choice."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    assert "{policy,llm}" in capsys.readouterr().out


def test_validate_config_ok(capsys):
    """Every shipped config file validates."""
    assert main(["validate-config"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.count("ok ") == 6 and "FAIL" not in out


def test_validate_config_reports_failures(tmp_path, capsys):
    """An empty config dir fails with a non-zero exit code."""
    assert main(["validate-config", "--config-dir", str(tmp_path)]) == EXIT_CONFIG_ERROR
    assert "FAIL limits.yaml" in capsys.readouterr().out


def test_run_policy_completes_offline(tmp_path, capsys):
    """WP-10 acceptance: `run --engine policy` completes P0–P10 offline."""
    code = main(["run", "--engine", "policy", "--runs-dir", str(tmp_path), "--run-id", "cli1"])
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert "run cli1: completed (phase P10)" in out
    assert (tmp_path / "cli1" / "decision_records" / "index.md").exists()
    assert main(["run", "--engine", "policy", "--runs-dir", str(tmp_path), "--run-id", "cli1"]) == (
        EXIT_USAGE
    )


def test_run_off_topic_is_refused(tmp_path, capsys):
    """An off-topic prompt exits with the refusal code and a redirect message."""
    code = main(
        ["run", "--engine", "policy", "--runs-dir", str(tmp_path), "--prompt", "Tell me a joke"]
    )
    assert code == EXIT_REFUSED
    assert "outside that scope" in capsys.readouterr().out


def test_pause_then_resume_from_the_cli(tmp_path, capsys):
    """`--input-mode pause` exits 3 with the question; `resume --answer` continues."""
    base = ["--engine", "policy", "--runs-dir", str(tmp_path)]
    assert main(["run", *base, "--run-id", "p", "--input-mode", "pause"]) == EXIT_PAUSED
    out = capsys.readouterr().out
    assert "pending question [REQ-01-DB-30]" in out and "resume --run-id p" in out
    assert main(["resume", *base, "--run-id", "p", "--answer", "Z"]) == EXIT_USAGE
    assert "invalid answer" in capsys.readouterr().err
    for answer in ("A", "55", "26"):
        assert main(["resume", *base, "--run-id", "p", "--answer", answer]) == EXIT_PAUSED
    assert main(["resume", *base, "--run-id", "p", "--answer", "yes"]) == EXIT_OK
    assert main(["resume", *base, "--run-id", "missing", "--answer", "x"]) == EXIT_USAGE
    bad_config = ["--config-dir", str(tmp_path / "none")]
    assert main(["resume", *base, *bad_config, "--run-id", "p", "--answer", "x"]) == (
        EXIT_CONFIG_ERROR
    )


def test_run_llm_engine_offline(tmp_path, capsys, monkeypatch):
    """`--engine llm` with the Ollama model replaced by a FunctionModel; calls are logged."""
    monkeypatch.setattr(engine_module, "build_ollama_model", lambda *a, **k: fake_model())
    code = main(["run", "--engine", "llm", "--runs-dir", str(tmp_path), "--run-id", "llm1"])
    assert code == EXIT_OK, capsys.readouterr().out
    calls = (tmp_path / "llm1" / "llm_calls.jsonl").read_text().splitlines()
    assert calls and all(json.loads(line)["decision_source"] == "llm" for line in calls)

    # resume without --engine keeps the engine the run was started with
    base = ["--runs-dir", str(tmp_path)]
    assert main(["run", "--engine", "llm", *base, "--run-id", "lp", "--input-mode", "pause"]) == (
        EXIT_PAUSED
    )
    before = len((tmp_path / "lp" / "llm_calls.jsonl").read_text().splitlines())
    assert main(["resume", *base, "--run-id", "lp", "--answer", "A"]) == EXIT_PAUSED
    after = len((tmp_path / "lp" / "llm_calls.jsonl").read_text().splitlines())
    assert after > before


def test_no_command_prints_help(capsys):
    """Without a command the CLI prints help and exits cleanly."""
    assert main([]) == EXIT_OK
    assert "usage: geoagent" in capsys.readouterr().out
