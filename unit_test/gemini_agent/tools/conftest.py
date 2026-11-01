"""Shared fixtures for the tool tests: an editable copy of ``config/``."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from gemini_agent.schemas.config import REPO_ROOT


@pytest.fixture
def config_copy(tmp_path: Path) -> Path:
    """A copy of the repository ``config/`` directory that a test may edit."""
    target = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", target)
    return target


@pytest.fixture
def edit_yaml() -> Callable[[Path, Callable[[dict], None]], None]:
    """Return ``edit(path, mutate)``: load a YAML file, apply ``mutate(data)``, write it back."""

    def edit(path: Path, mutate: Callable[[dict], None]) -> None:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    return edit
