"""
Runtime execution unit: compiled from PlanDocument. No PlanStep in the execution path.

Scheduler uses only dependencies + task status; plan step index is not used for ordering.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent_v2.schemas.execution import ExecutionResult

TaskStatus = Literal["pending", "ready", "running", "completed", "failed"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ExecutionTask(BaseModel):
    """Single DAG node — the only mutable runtime unit during execution."""

    id: str
    tool: str
    task_name: str | None = None  # Task name for model routing (e.g., "PLANNER_DECISION_ACT")
    model_key: str | None = None  # Optional model override for this task
    dependencies: list[str] = Field(default_factory=list)
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_frozen: bool = False  # Separate control flag - not in arguments dict
    status: TaskStatus = "pending"
    attempts: int = 0
    max_attempts: int = 2
    goal: str = ""
    input_hints: dict[str, Any] = Field(default_factory=dict)
    last_result: ExecutionResult | None = None
    started_at: str | None = None
    completed_at: str | None = None


class TaskScheduler:
    """Dependency-based scheduler with explicit state transitions."""

    @staticmethod
    def ready_tasks(tasks_by_id: dict[str, ExecutionTask], completed_ids: set[str]) -> list[ExecutionTask]:
        """Return pending tasks whose dependencies are all completed. NO sorting for logic."""
        ready: list[ExecutionTask] = []
        for t in tasks_by_id.values():
            if t.status != "pending":
                continue
            if all(d in completed_ids for d in t.dependencies):
                ready.append(t)
        # Stable sort ONLY for determinism in logs (fine for now - single-threaded)
        ready.sort(key=lambda x: x.id)
        return ready

    @staticmethod
    def transition_to_running(task: ExecutionTask) -> ExecutionTask:
        """Pending → running transition."""
        if task.status != "pending":
            raise ValueError(f"Cannot transition {task.id} from {task.status} to running")
        return task.model_copy(update={"status": "running", "started_at": _utc_now()})

    @staticmethod
    def transition_to_completed(task: ExecutionTask, result: ExecutionResult) -> ExecutionTask:
        """Running → completed transition."""
        return task.model_copy(update={
            "status": "completed",
            "completed_at": _utc_now(),
            "last_result": result
        })

    @staticmethod
    def transition_to_pending_after_failure(task: ExecutionTask) -> ExecutionTask:
        """Running → pending retry transition."""
        if task.status != "running":
            raise ValueError(f"Cannot transition {task.id} from {task.status} to pending")
        if task.attempts >= task.max_attempts:
            raise ValueError(f"Task {task.id} has exceeded max attempts")
        return task.model_copy(update={"status": "pending"})

    @staticmethod
    def transition_to_failed(task: ExecutionTask, result: ExecutionResult) -> ExecutionTask:
        """Running → failed terminal transition."""
        return task.model_copy(update={
            "status": "failed",
            "completed_at": _utc_now(),
            "last_result": result
        })


class SchedulerResult(BaseModel):
    """Result of a scheduler run."""
    status: Literal["success", "failed", "deadlock"]
    completed_ids: set[str] = Field(default_factory=set)
    failed_task: ExecutionTask | None = None
    last_result: ExecutionResult | None = None
