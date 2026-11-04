"""Repository-level checks for WP-00: CI workflow, pinned requirements, ignores, NOTICE."""

import tomllib

import yaml

from gemini_agent.schemas.config import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "hackathon_tests.yaml"
TEST_COMMAND = (
    "python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator "
    "unit_test/gemini_agent -x"
)


def test_ci_workflow_is_valid():
    """Workflow parses, triggers on push + pull_request, uses Python 3.12, runs tests and ruff."""
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as boolean True
    assert {"push", "pull_request"} <= set(triggers)
    (job,) = wf["jobs"].values()
    steps = job["steps"]
    setup = next(s for s in steps if s.get("uses", "").startswith("actions/setup-python"))
    assert setup["with"]["python-version"] == "3.12"
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "requirements-hackathon.txt" in runs
    assert TEST_COMMAND in runs
    assert "ruff check src/gemini_agent unit_test/gemini_agent" in runs
    test_step = next(s for s in steps if TEST_COMMAND in s.get("run", ""))
    assert test_step["env"]["PYTHONPATH"] == "src"


def test_requirements_are_pinned():
    """Every requirement is pinned with == (NFR-01); spec-mandated versions are exact."""
    lines = [
        line.split("#")[0].strip()
        for line in (REPO_ROOT / "requirements-hackathon.txt").read_text().splitlines()
    ]
    pins = dict(line.split("==") for line in lines if line)
    assert all(pins.values())
    assert pins["numpy"] == "2.2.6"
    assert pins["scipy"] == "1.13.1"
    assert pins["pandas"] == "2.3.3"
    assert pins["matplotlib"] == "3.10.7"
    assert pins["pydantic-ai"] == "2.64.0"
    assert pins["pydantic"].startswith("2.")
    assert {"pyyaml", "pytest", "pytest-cov", "ruff"} <= set(pins)


def test_gitignore_and_pytest_settings():
    """runs/ is ignored except runs/final/; pytest skips third_party/ and runs/."""
    ignore = (REPO_ROOT / ".gitignore").read_text().splitlines()
    assert {"runs/*", "!runs/final/", ".venv/"} <= set(ignore)
    pytest_cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["tool"]["pytest"]
    opts = pytest_cfg["ini_options"]
    assert opts["pythonpath"] == "src"
    assert {"third_party", "runs"} <= set(opts["norecursedirs"])


def test_notice_credits():
    """NOTICE credits GEMINI/TNO, Plant Debugger (with commit), Warrant and Plant-as-Code."""
    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    for needle in (
        "GEMINI",
        "TNO",
        "PlantDebugger",
        "d69507aa48aec773a5cf72761e5ff60c491eefc1",
        "Apache",
        "Warrant",
        "Plant-as-Code",
    ):
        assert needle in notice, needle


def test_no_case_only_filename_collisions():
    """Tracked paths must not differ only by case (macOS/Windows checkouts merge them)."""
    import subprocess

    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    seen: dict[str, str] = {}
    clashes = []
    for path in out:
        key = path.lower()
        if key in seen and seen[key] != path:
            clashes.append((seen[key], path))
        seen.setdefault(key, path)
    assert not clashes, f"case-only path collisions: {clashes}"
