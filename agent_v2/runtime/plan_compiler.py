"""
Compile PlanDocument → list[ExecutionTask]. Pure transform; no execution.

Plan step order in the document is not used for scheduling (dependencies only).
"""
from __future__ import annotations

from typing import Any

from agent_v2.schemas.execution_task import ExecutionTask
from agent_v2.schemas.plan import PlanDocument
from agent_v2.schemas.policies import ExecutionPolicy


def compile_plan(
    plan: PlanDocument,
    *,
    policy: ExecutionPolicy | None = None,
) -> list[ExecutionTask]:
    """
    step_id → task.id, action → tool, dependencies preserved, arguments = {}.
    """
    max_attempts = 2
    if policy is not None:
        max_attempts = max(1, int(policy.max_retries_per_step))

    out: list[ExecutionTask] = []
    for s in plan.steps:
        hints: dict[str, Any] = {}
        if isinstance(s.inputs, dict):
            hints = dict(s.inputs)
        out.append(
            ExecutionTask(
                id=s.step_id,
                tool=s.action,
                dependencies=list(s.dependencies or []),
                arguments={},
                status="pending",
                attempts=0,
                max_attempts=max_attempts,
                goal=str(s.goal or ""),
                input_hints=hints,
            )
        )
    return out


def tasks_by_id(tasks: list[ExecutionTask]) -> dict[str, ExecutionTask]:
    return {t.id: t for t in tasks}
