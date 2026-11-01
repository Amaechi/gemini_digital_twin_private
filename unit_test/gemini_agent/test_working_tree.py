"""The test suite leaves the git working tree as it found it (WP-12 sandbox rule).

``conftest.py`` snapshots the tree in ``pytest_configure`` and moves this test to the end of the
run, so it checks the whole suite: no tracked file modified, no new untracked file (e.g. a
generated model written into ``src/`` or ``unit_test/``), no change to ``registry.py``.
"""

import pytest

from gemini_agent.schemas.config import REPO_ROOT
from unit_test.gemini_agent.working_tree import SNAPSHOT_KEY, git_snapshot


def test_git_working_tree_unchanged(request):
    """Same HEAD, status, diff and untracked-file contents as when pytest started."""
    before = request.config.stash.get(SNAPSHOT_KEY, None)
    if before is None:
        pytest.skip("git or the repository is not available")
    after = git_snapshot(REPO_ROOT)
    assert after is not None
    assert after["head"] == before["head"]
    assert after["status"] == before["status"], "tests changed tracked or untracked files"
    assert after["diff_sha256"] == before["diff_sha256"], "tests modified tracked files"
    assert after["untracked_sha256"] == before["untracked_sha256"]
