"""
Runtime execution unit: compiled from PlanDocument. No PlanStep in the execution path.

Scheduler uses only dependencies + task status; plan step index is not used for ordering.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from agent_v2.schemas.execution import ExecutionResult

TaskStatus = Literal["pending", "ready", "running", "completed", "failed"]


class ExecutionTask(BaseModel):
    """Single DAG node — the only mutable runtime unit during execution."""

    id: str
    tool: str
    dependencies: list[str] = Field(default_factory=list)
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: TaskStatus = "pending"
    attempts: int = 0
    max_attempts: int = 2
    goal: str = ""
    input_hints: dict[str, Any] = Field(default_factory=dict)
    last_result: ExecutionResult | None = None
    started_at: str | None = None
    completed_at: str | None = None


class TaskScheduler:
    """Ready = pending tasks whose dependencies are all completed. Tie-break: task id."""

    @staticmethod
    def ready_tasks(tasks_by_id: dict[str, ExecutionTask], completed_ids: set[str]) -> list[ExecutionTask]:
        ready: list[ExecutionTask] = []
        for t in tasks_by_id.values():
            if t.status != "pending":
                continue
            if all(d in completed_ids for d in t.dependencies):
                ready.append(t)
        ready.sort(key=lambda x: x.id)
        return ready
