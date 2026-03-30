"""
Per-request task name for PlannerV2 → call_reasoning_model.

Maps plan vs act vs replan to ``models_config.json`` keys:
``PLANNER_DECISION_PLAN``, ``PLANNER_DECISION_ACT``, ``PLANNER_REPLAN_PLAN``,
``PLANNER_REPLAN_ACT`` (legacy orchestrator lane replanner:
``PLANNER_REPLAN_ORCHESTRATOR``; legacy planner: ``planner``).
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

_planner_model_task: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "planner_model_task", default=None
)


@contextmanager
def planner_model_task_scope(task_name: str) -> Iterator[None]:
    token = _planner_model_task.set(task_name)
    try:
        yield
    finally:
        _planner_model_task.reset(token)


def get_active_planner_model_task() -> str | None:
    return _planner_model_task.get()
