"""The ported USPTO retrosynthesis pack must load with the pinned Harbor Task parser."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import ROOT
from harbor.models.task.task import Task


USPTO_DIR = ROOT / "data" / "uspto"


def _task_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.iterdir() if path.is_dir() and (path / "task.toml").is_file()
    )


TASK_DIRS = _task_dirs(USPTO_DIR)


@pytest.mark.parametrize("task_dir", TASK_DIRS, ids=lambda path: path.name)
def test_uspto_task_loads(task_dir: Path):
    task = Task(task_dir)
    assert task.instruction.strip(), "empty instruction"
    assert (task_dir / "environment" / "Dockerfile").is_file()
    assert (task_dir / "tests" / "test.sh").is_file()
