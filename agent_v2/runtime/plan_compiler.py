"""
Compile PlanDocument → CompiledExecutionGraph.

Pure transform: no I/O, no LLM, no execution. PlanStep.execution / failure are ignored.
"""
from __future__ import annotations

from typing import Any

from agent_v2.schemas.execution_task import CompiledExecutionGraph, ExecutionTask, TaskRuntimeState
from agent_v2.schemas.plan import PlanDocument, PlanStep
from agent_v2.schemas.policies import ExecutionPolicy


def plan_step_for_argument_generation(task: ExecutionTask) -> PlanStep:
    """Synthetic PlanStep for PlanArgumentGenerator (legacy tool schema seam)."""
    tool = task.tool
    stype: Any = "explore"
    if tool == "open_file":
        stype = "analyze"
    elif tool == "edit":
        stype = "modify"
    elif tool == "run_tests":
        stype = "validate"
    elif tool == "finish":
        stype = "finish"
    return PlanStep(
        step_id=task.id,
        index=task.plan_step_index,
        type=stype,
        goal=task.goal,
        action=tool,  # type: ignore[arg-type]
        inputs=dict(task.input_hints),
        outputs={},
        dependencies=list(task.dependencies),
    )


def compile_plan_document(
    plan: PlanDocument,
    *,
    policy: ExecutionPolicy | None = None,
) -> CompiledExecutionGraph:
    """
    Map step_id → task.id, action → tool, copy dependencies, empty arguments.
    """
    max_attempts = 2
    if policy is not None:
        max_attempts = max(1, int(policy.max_retries_per_step))

    by_id: dict[str, ExecutionTask] = {}
    ordered = sorted(plan.steps, key=lambda s: s.index)
    for s in ordered:
        hints: dict[str, Any] = {}
        if isinstance(s.inputs, dict):
            hints = dict(s.inputs)
        t = ExecutionTask(
            id=s.step_id,
            tool=s.action,
            dependencies=list(s.dependencies or []),
            plan_step_index=int(s.index),
            goal=str(s.goal or ""),
            input_hints=hints,
            arguments={},
            runtime=TaskRuntimeState(
                status="pending",
                attempts=0,
                max_attempts=max_attempts,
            ),
        )
        by_id[t.id] = t

    return CompiledExecutionGraph(plan_id=plan.plan_id, tasks_by_id=by_id)
