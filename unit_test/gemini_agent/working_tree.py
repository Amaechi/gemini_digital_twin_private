"""Snapshot of the git working tree, taken when pytest starts and compared after the suite.

Tests must never modify tracked files or leave new untracked (non-ignored) files in the
repository (WP-12: generated models live in ``tmp_path`` sandboxes and are registered at runtime
only). The snapshot covers HEAD, ``git status`` (tracked changes and untracked files), the full
``git diff HEAD`` and the content of every untracked file, so a test that edits an already
modified file is caught too.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

SNAPSHOT_KEY = pytest.StashKey[dict[str, object] | None]()
TEST_NAME = "test_git_working_tree_unchanged"


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True, timeout=120
    ).stdout


def git_snapshot(repo: Path) -> dict[str, object] | None:
    """Digest of the working tree (None if git or the repository is unavailable)."""
    try:
        head = _git(repo, "rev-parse", "HEAD").strip().decode()
        status = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        diff = _git(repo, "diff", "HEAD", "--binary")
    except (OSError, subprocess.SubprocessError):
        return None
    entries = [e.decode("utf-8", "replace") for e in status.split(b"\0") if e]
    untracked = {}
    for entry in entries:
        if entry.startswith("?? "):
            path = repo / entry[3:]
            data = path.read_bytes() if path.is_file() else b""
            untracked[entry[3:]] = hashlib.sha256(data).hexdigest()
    return {
        "head": head,
        "status": sorted(entries),
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "untracked_sha256": untracked,
    }
