"""A hermetic copy of the committed fixture run ``runs/final_policy/`` (3.4 MB) in a temp dir.

The tests read the copy, so they neither depend on other (git-ignored) local runs nor touch the
repository; the copy's mtimes prove the readers write nothing.
"""

import shutil
from pathlib import Path

import pytest

from gemini_agent.schemas.config import REPO_ROOT

SOURCE_RUN = REPO_ROOT / "runs" / "final_policy"


@pytest.fixture(scope="session")
def fixture_runs(tmp_path_factory) -> Path:
    """Runs directory holding ``final_policy`` (copied once per session)."""
    if not SOURCE_RUN.is_dir():
        pytest.skip("fixture run runs/final_policy is not checked out")
    root = tmp_path_factory.mktemp("runs")
    shutil.copytree(SOURCE_RUN, root / "final_policy")
    return root
