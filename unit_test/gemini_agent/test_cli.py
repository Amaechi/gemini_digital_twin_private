"""Unit tests for gemini_agent.cli."""

import os
import subprocess
import sys

import pytest

from gemini_agent import __version__
from gemini_agent.cli import EXIT_CONFIG_ERROR, EXIT_NOT_IMPLEMENTED, EXIT_OK, main
from gemini_agent.schemas.config import REPO_ROOT


def test_module_help_runs_as_subprocess():
    """`python -m gemini_agent.cli --help` works (WP-00 acceptance)."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "gemini_agent.cli", "--help"],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT, timeout=60, check=False,
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


def test_run_is_a_stub_until_wp10():
    """`run` exists but reports that the runner is not implemented yet."""
    assert main(["run", "--engine", "policy"]) == EXIT_NOT_IMPLEMENTED


def test_no_command_prints_help(capsys):
    """Without a command the CLI prints help and exits cleanly."""
    assert main([]) == EXIT_OK
    assert "usage: geoagent" in capsys.readouterr().out
