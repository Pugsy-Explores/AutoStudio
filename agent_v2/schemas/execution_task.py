"""
Execution graph — compiled from PlanDocument. Runtime state is NOT stored on PlanStep.

ExecutionTask is the unit the DAG scheduler runs. TaskRuntimeState holds mutable
per-task execution fields (arguments are snapshotted on ExecutionTask before dispatch).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from agent_v2.schemas.execution import ExecutionResult


class TaskRuntimeState(BaseModel):
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    attempts: int = 0
    max_attempts: int = 2
    started_at: str | None = None
    completed_at: str | None = None
    last_result: ExecutionResult | None = None


class ExecutionTask(BaseModel):
    """Single node in the execution DAG (compiled from one PlanStep)."""

    id: str
    tool: str
    dependencies: list[str] = Field(default_factory=list)
    plan_step_index: int
    goal: str = ""
    input_hints: dict[str, Any] = Field(default_factory=dict)
    arguments: dict[str, Any] = Field(default_factory=dict)
    runtime: TaskRuntimeState = Field(default_factory=TaskRuntimeState)


class CompiledExecutionGraph(BaseModel):
    """Compiler output: tasks plus adjacency (dependencies are listed per task)."""

    plan_id: str
    tasks_by_id: dict[str, ExecutionTask]

    def ordered_tasks(self) -> list[ExecutionTask]:
        return sorted(self.tasks_by_id.values(), key=lambda t: t.plan_step_index)


class TaskScheduler:
    """Minimal scheduler: ready = pending tasks whose dependencies are completed."""

    @staticmethod
    def ready_tasks(graph: CompiledExecutionGraph, completed_ids: set[str]) -> list[ExecutionTask]:
        ready: list[ExecutionTask] = []
        for t in graph.ordered_tasks():
            if t.runtime.status != "pending":
                continue
            if all(d in completed_ids for d in t.dependencies):
                ready.append(t)
        ready.sort(key=lambda x: x.plan_step_index)
        return ready
