"""Task planner module must not import runtime loops (architecture freeze)."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_task_planner_module_does_not_import_runtime_loops():
    root = Path(__file__).resolve().parents[1] / "agent_v2" / "planning" / "task_planner.py"
    text = root.read_text(encoding="utf-8")
    forbidden = (
        "PlannerTaskRuntime",
        "ExplorationRunner",
        "PlanExecutor",
        "planner_task_runtime",
        "exploration_runner",
    )
    for f in forbidden:
        assert f not in text, f"unexpected reference to {f} in task_planner.py"
