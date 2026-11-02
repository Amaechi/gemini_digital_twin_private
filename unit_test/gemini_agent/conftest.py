"""Test-wide safety net: no test may send a request to a real LLM (CLAUDE.md, spec §4.3).

``TestModel`` and ``FunctionModel`` are unaffected. A real (Ollama) model is never built: the
engine's ``build_ollama_model`` raises unless a test replaces it on purpose, and the error
propagates (``EngineFailure`` fallback does not catch it), so the test fails loudly instead of
silently falling back to the policy.

Second net (WP-12): the git working tree is snapshotted when pytest starts and
``test_working_tree.py::test_git_working_tree_unchanged`` runs last and compares it.
"""

import importlib.util
from pathlib import Path

import pytest as _pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def pytest_configure(config):
    """Snapshot the git working tree before any test runs (stdlib + pytest only)."""
    from unit_test.gemini_agent.working_tree import SNAPSHOT_KEY, git_snapshot

    config.stash[SNAPSHOT_KEY] = git_snapshot(_REPO_ROOT)


@_pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    """Run the working-tree check after every other collected test."""
    from unit_test.gemini_agent.working_tree import TEST_NAME

    last = [item for item in items if item.name == TEST_NAME]
    items[:] = [item for item in items if item.name != TEST_NAME] + last


if importlib.util.find_spec("pydantic_ai") is None:
    # Agent tests run in the "Hackathon tests" workflow, which installs
    # requirements-hackathon.txt. GEMINI's upstream "Test" workflow uses its Poetry environment
    # without pydantic-ai, so this directory is not collected there (no ModuleNotFoundError).
    collect_ignore_glob = ["*"]
else:
    import pydantic_ai.models
    import pytest

    from gemini_agent.agents import engine as engine_module

    pydantic_ai.models.ALLOW_MODEL_REQUESTS = False

    def _no_real_model(*args, **kwargs):
        raise AssertionError(
            "tests must not build a real LLM model; pass a FunctionModel/TestModel"
        )

    @pytest.fixture(autouse=True, scope="session")
    def _smoke_budget():
        """WP-14: a test that builds a default (real) toolbox and runs the whole pipeline gets the
        smoke budget (register RUN-BUDGET), never the full-size optimisation + Monte Carlo."""
        from gemini_agent.runner.real_tools import BUDGET_ENV

        with pytest.MonkeyPatch.context() as patch:
            patch.setenv(BUDGET_ENV, "smoke")
            yield

    @pytest.fixture(scope="module")
    def stub_tools():
        """Bind the labelled stub catalogue wherever a default toolbox is built (WP-14).

        ``default_toolbox`` binds the real engineering tools; tests of the workflow mechanics
        (runner, CLI, eval harness, submission) opt in with
        ``pytestmark = pytest.mark.usefixtures("stub_tools")`` to stay fast.
        """
        from gemini_agent import cli
        from gemini_agent.eval import harness
        from gemini_agent.runner import design_runner, tool_stubs

        def _stubs(*args, **kwargs):
            return tool_stubs.stub_toolbox()

        # Module scope: module-scoped fixtures (e.g. the eval suites) must see the stubs too.
        with pytest.MonkeyPatch.context() as patch:
            for module in (design_runner, cli, harness):
                patch.setattr(module, "default_toolbox", _stubs)
            yield

    @pytest.fixture(autouse=True)
    def _block_real_llm(monkeypatch):
        """Make any accidental real-model construction fail the test."""
        monkeypatch.setattr(engine_module, "build_ollama_model", _no_real_model)
